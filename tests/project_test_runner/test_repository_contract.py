from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import unittest

from scripts.project_test_registry import load_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "test-suites.v1.json"
EXCEPTIONS_PATH = PROJECT_ROOT / "config" / "test-local-write-exceptions.v1.json"


@dataclass(frozen=True)
class PathFact:
    rooted_in_project: bool | None
    parts: tuple[str | None, ...]
    unresolved_local: bool = False
    trusted_external: bool = False
    value_kind: str | None = None

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
    "hardlink_to",
    "mkdir",
    "symlink_to",
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
    "link",
    "move",
    "rename",
    "replace",
    "symlink",
}
_TWO_PATH_MUTATOR_CALLS = {
    f"{module}.{operation}"
    for module in ("os", "shutil")
    for operation in _TWO_PATH_MUTATORS
}
_FUNCTION_PATH_MUTATORS = {
    "chmod",
    "makedirs",
    "mkdir",
    "remove",
    "removedirs",
    "rmdir",
    "rmtree",
    "unlink",
    "utime",
}
_FUNCTION_PATH_MUTATOR_CALLS = {
    f"os.{operation}" for operation in _FUNCTION_PATH_MUTATORS
} | {"shutil.rmtree"}
_TEMPORARY_CREATORS = {
    "mkdtemp",
    "mkstemp",
    "NamedTemporaryFile",
    "TemporaryDirectory",
}
_TRUSTED_EXTERNAL_BOUNDARY_PROVIDERS = {
    "scripts.project_test_run_identity.create_synthetic_project_test_run",
    "tests.project_test_runner._fixture_root.new_fixture_dir",
    "tests.project_test_runner.test_registry.fixture_run_dir",
    "tests.video_workflow._test_run.module_test_root",
    "tests.video_workflow._test_run.new_case_dir",
    "tests.video_workflow._test_run.new_workflow_workspace",
    "tests.video_workflow.test_source_publication_integration.build_decision_ready_authority",
}
_DATABASE_CONNECTORS = {"sqlite3.connect"}
_OUTPUT_METHODS = {"save"}
_SUBPROCESS_CALLS = {
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}
_SUBPROCESS_OUTPUT_FLAGS = {
    "--dest",
    "--destination",
    "--out",
    "--out-dir",
    "--output",
    "--output-dir",
    "-o",
}


def _target_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _target_key(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _sequence_elements(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, (ast.List, ast.Tuple)):
        return list(node.elts)
    return []


class _LocalWriteAnalyzer(ast.NodeVisitor):
    def __init__(
        self,
        source_text: str,
        source: Path,
        allowed: dict[tuple[str, str, str, str], str],
        suite_id: str,
    ) -> None:
        self.source_text = source_text
        self.source = source
        self.allowed = allowed
        self.suite_id = suite_id
        self.used: set[tuple[str, str]] = set()
        self.violations: list[str] = []
        self.environment: dict[str, PathFact] = {
            "PROJECT_ROOT": PathFact(True, ()),
        }
        self.test_id = ""
        self.helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.active_helpers: set[str] = set()
        self.import_aliases: dict[str, str] = {}
        self.evaluated_helper_calls: set[int] = set()

    def prepare(self, tree: ast.Module) -> None:
        """Collect module symbols before analyzing any executable test body."""

        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom):
                module = statement.module or ""
                for imported in statement.names:
                    local_name = imported.asname or imported.name
                    self.import_aliases[local_name] = f"{module}.{imported.name}"
                    self.environment[local_name] = self._unknown()
            elif isinstance(statement, ast.Import):
                for imported in statement.names:
                    local_name = imported.asname or imported.name.split(".", 1)[0]
                    self.import_aliases[local_name] = imported.name
                    self.environment[local_name] = self._unknown()
            elif (
                isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not statement.name.startswith("test_")
            ):
                self.helpers[statement.name] = statement

    @staticmethod
    def _unknown(*, local: bool = True) -> PathFact:
        return PathFact(None, (), local)

    @staticmethod
    def _opaque() -> PathFact:
        return PathFact(None, (), False)

    @staticmethod
    def _as_path_fact(fact: PathFact | None) -> PathFact | None:
        if fact is None or fact.value_kind != "relative_string":
            return fact
        return PathFact(
            True,
            fact.parts,
            fact.unresolved_local,
            fact.trusted_external,
            fact.value_kind,
        )

    def _canonical_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.import_aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            owner = self._canonical_name(node.value)
            if owner is not None:
                return f"{owner}.{node.attr}"
        return None

    def _is_trusted_provider(
        self,
        node: ast.Call,
        canonical: str | None,
    ) -> bool:
        if canonical in _TRUSTED_EXTERNAL_BOUNDARY_PROVIDERS:
            return True
        if not isinstance(node.func, ast.Name):
            return False
        source_module = self.source.with_suffix("").as_posix().replace("/", ".")
        return (
            f"{source_module}.{node.func.id}"
            in _TRUSTED_EXTERNAL_BOUNDARY_PROVIDERS
        )

    def _literal_parts(self, node: ast.AST) -> tuple[str | None, ...]:
        fact = self._fact(node)
        if fact is not None and fact.rooted_in_project is False:
            return fact.parts
        return (None,)

    def _aggregate_fact(self, nodes: list[ast.AST]) -> PathFact:
        facts = [self._fact(node) or self._unknown() for node in nodes]
        if any(fact.rooted_in_project is True for fact in facts):
            return self._unknown()
        if any(fact.trusted_external for fact in facts):
            return PathFact(False, (None,), trusted_external=True)
        if any(fact.unresolved_local for fact in facts):
            return self._unknown()
        if facts and all(fact.rooted_in_project is False for fact in facts):
            return PathFact(
                False,
                (None,),
                trusted_external=any(
                    fact.trusted_external for fact in facts
                ),
            )
        return self._opaque()

    def _fact(self, node: ast.AST) -> PathFact | None:
        key = _target_key(node)
        if key is not None and key in self.environment:
            return self.environment[key]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            is_absolute = (
                PureWindowsPath(node.value).is_absolute()
                or PurePosixPath(node.value).is_absolute()
            )
            return PathFact(
                False,
                tuple(
                    part
                    for part in node.value.replace("\\", "/").split("/")
                    if part and part != "."
                ),
                value_kind=(
                    "absolute_string" if is_absolute else "relative_string"
                ),
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            return PathFact(None, (), value_kind="bytes")
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
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)
                and base is not None
            ):
                trim = node.slice.value + 1
                return PathFact(
                    base.rooted_in_project,
                    base.parts[:-trim] if trim <= len(base.parts) else (),
                    base.unresolved_local,
                    base.trusted_external,
                )
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
            if base is not None:
                return base
        if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            return self._aggregate_fact(list(node.elts))
        if isinstance(node, ast.Dict):
            return self._aggregate_fact(
                [
                    value
                    for key, value in zip(node.keys, node.values)
                    if key is not None
                ]
            )
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            return self._aggregate_fact(
                [generator.iter for generator in node.generators]
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self._fact(node.left)
            if left is None:
                return self._unknown()
            return PathFact(
                left.rooted_in_project,
                left.parts + self._literal_parts(node.right),
                left.unresolved_local,
                left.trusted_external,
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._aggregate_fact([node.left, node.right])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            canonical = self._canonical_name(node.func)
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"self", "cls"}
                and node.func.attr in self.helpers
            ):
                returned = self._helper_return_fact(node.func.attr, node)
                if returned.rooted_in_project is not None:
                    return returned
                trusted_arguments = [
                    self._fact(argument) for argument in node.args
                ]
                if any(
                    fact is not None and fact.trusted_external
                    for fact in trusted_arguments
                ):
                    return PathFact(False, (None,), trusted_external=True)
                return returned
            if self._is_trusted_provider(node, canonical):
                return PathFact(False, (None,), trusted_external=True)
            if canonical in {"pathlib.Path.cwd", "Path.cwd"}:
                return PathFact(True, ())
            base = self._fact(node.func.value)
            if base is not None and node.func.attr == "joinpath":
                parts = tuple(
                    part
                    for argument in node.args
                    for part in self._literal_parts(argument)
                )
                return PathFact(
                    base.rooted_in_project,
                    base.parts + parts,
                    base.unresolved_local,
                    base.trusted_external,
                )
            if base is not None and node.func.attr in {"absolute", "resolve"}:
                return base
            if node.func.attr == "read_bytes":
                return PathFact(None, (), value_kind="bytes")
            if node.func.attr == "read_text":
                return self._opaque()
            if base is not None and base.rooted_in_project is not None:
                return base
            if (
                base is not None
                and base.rooted_in_project is None
                and not base.unresolved_local
            ):
                return self._opaque()
            if node.args:
                owner = self._fact(node.args[0])
                if owner is not None and owner.trusted_external:
                    return PathFact(False, (None,), trusted_external=True)
            return self._unknown()
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        ):
            canonical = self.import_aliases.get(node.func.id, node.func.id)
            if canonical in {"str", "os.fspath"} and len(node.args) == 1:
                fact = self._fact(node.args[0]) or self._unknown()
                return PathFact(
                    fact.rooted_in_project,
                    fact.parts,
                    fact.unresolved_local,
                    fact.trusted_external,
                    (
                        fact.value_kind
                        if fact.value_kind
                        in {"absolute_string", "relative_string"}
                        else "path_string"
                    ),
                )
            if canonical.rsplit(".", 1)[-1] in {
                "Path",
                "PurePath",
                "PurePosixPath",
                "PureWindowsPath",
            }:
                fact = (
                    self._as_path_fact(self._fact(node.args[0]))
                    if node.args
                    else self._unknown()
                )
                assert fact is not None
                return PathFact(
                    fact.rooted_in_project,
                    fact.parts,
                    fact.unresolved_local,
                    fact.trusted_external,
                    "path",
                )
            if self._is_trusted_provider(node, canonical):
                return PathFact(False, (None,), trusted_external=True)
            if node.func.id in {"list", "next", "set", "sorted", "tuple"} and node.args:
                return self._fact(node.args[0]) or self._unknown()
            if (
                node.func.id in self.helpers
                and node.func.id not in self.import_aliases
            ):
                returned = self._helper_return_fact(node.func.id, node)
                if returned.rooted_in_project is not None:
                    return returned
                trusted_arguments = [
                    self._fact(argument) for argument in node.args
                ]
                if any(
                    fact is not None and fact.trusted_external
                    for fact in trusted_arguments
                ):
                    return PathFact(False, (None,), trusted_external=True)
                return returned
            if node.func.id in self.import_aliases:
                if node.args:
                    owner = self._fact(node.args[0])
                    if owner is not None and owner.trusted_external:
                        return PathFact(
                            False,
                            (None,),
                            trusted_external=True,
                        )
                return self._unknown()
            return self._unknown()
        if isinstance(node, ast.Attribute):
            base = self._fact(node.value)
            if base is not None and node.attr == "parent":
                return PathFact(
                    base.rooted_in_project,
                    base.parts[:-1],
                    base.unresolved_local,
                    base.trusted_external,
                )
            if base is not None:
                return base
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return PathFact(True, tuple(self.source.parts))
            return self._unknown()
        return None

    def _bind_helper(
        self,
        helper: ast.FunctionDef | ast.AsyncFunctionDef,
        call: ast.Call,
    ) -> dict[str, PathFact]:
        bound: dict[str, PathFact] = {}
        positional = list(helper.args.args)
        if (
            isinstance(call.func, ast.Attribute)
            and positional
            and positional[0].arg in {"self", "cls"}
        ):
            bound[positional[0].arg] = (
                self._fact(call.func.value) or self._opaque()
            )
            positional = positional[1:]
        required_count = len(positional) - len(helper.args.defaults)
        for parameter, default in zip(
            positional[required_count:],
            helper.args.defaults,
        ):
            bound[parameter.arg] = self._fact(default) or self._unknown()
        for parameter, default in zip(
            helper.args.kwonlyargs,
            helper.args.kw_defaults,
        ):
            if default is not None:
                bound[parameter.arg] = self._fact(default) or self._unknown()
        for parameter, argument in zip(positional, call.args):
            bound[parameter.arg] = self._fact(argument) or self._unknown()
        for keyword in call.keywords:
            if keyword.arg is not None:
                bound[keyword.arg] = self._fact(keyword.value) or self._unknown()
        return bound

    def _bind_assignment(self, target: ast.AST, fact: PathFact) -> None:
        key = _target_key(target)
        if key is not None:
            if key != "PROJECT_ROOT" or fact.rooted_in_project is False:
                self.environment[key] = fact
            return
        if isinstance(target, (ast.List, ast.Tuple)):
            for element in target.elts:
                self._bind_assignment(element, fact)

    def _helper_return_fact(self, name: str, call: ast.Call) -> PathFact:
        if name in self.active_helpers:
            return self._unknown(local=True)
        helper = self.helpers[name]
        previous_environment = self.environment
        previous_import_aliases = self.import_aliases
        self.environment = {
            **previous_environment,
            **self._bind_helper(helper, call),
        }
        self.import_aliases = dict(previous_import_aliases)
        self.active_helpers.add(name)
        self.evaluated_helper_calls.add(id(call))
        returned: list[PathFact] = []
        receiver_prefix = (
            f"{helper.args.args[0].arg}."
            if isinstance(call.func, ast.Attribute)
            and helper.args.args
            and helper.args.args[0].arg in {"self", "cls"}
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in {"self", "cls"}
            else None
        )
        receiver_effects: dict[str, PathFact] = {}
        try:
            for statement in helper.body:
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    self.visit(statement)
                elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    value = statement.value
                    if value is not None:
                        fact = self._fact(value) or self._unknown()
                        targets = (
                            statement.targets
                            if isinstance(statement, ast.Assign)
                            else (statement.target,)
                        )
                        for target in targets:
                            self._bind_assignment(target, fact)
                elif isinstance(statement, ast.Return):
                    returned.append(
                        self._fact(statement.value)
                        if statement.value is not None
                        else self._unknown()
                    )
                elif isinstance(statement, (ast.Expr, ast.Yield)):
                    yielded = (
                        statement.value
                        if isinstance(statement, ast.Yield)
                        else statement.value.value
                        if isinstance(statement.value, ast.Yield)
                        else None
                    )
                    if yielded is not None:
                        returned.append(self._fact(yielded) or self._unknown())
                    elif (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Attribute)
                        and isinstance(statement.value.func.value, ast.Name)
                        and statement.value.func.value.id in {"self", "cls"}
                        and statement.value.func.attr in self.helpers
                    ):
                        self._helper_return_fact(
                            statement.value.func.attr,
                            statement.value,
                        )
                elif isinstance(
                    statement,
                    (ast.If, ast.Try, ast.Match, ast.For, ast.While),
                ) and any(
                    isinstance(descendant, ast.Return)
                    for descendant in ast.walk(statement)
                ):
                    returned.append(self._unknown())
        finally:
            if receiver_prefix is not None:
                receiver_effects = {
                    key: fact
                    for key, fact in self.environment.items()
                    if key.startswith(receiver_prefix)
                }
            self.active_helpers.remove(name)
            self.environment = previous_environment
            self.import_aliases = previous_import_aliases
            self.environment.update(receiver_effects)
        if not returned or any(fact is None for fact in returned):
            return self._unknown(local=True)
        first = returned[0]
        if any(fact != first for fact in returned[1:]):
            return self._unknown(local=True)
        if first.rooted_in_project is None and first.unresolved_local:
            return self._unknown(local=True)
        return first

    def _record(self, node: ast.Call, fact: PathFact | None) -> None:
        fact = self._as_path_fact(fact)
        if fact is not None and fact.rooted_in_project is False:
            return
        if (
            fact is not None
            and fact.rooted_in_project is None
            and not fact.unresolved_local
        ):
            return
        if fact is None or fact.rooted_in_project is None:
            expression = ast.get_source_segment(self.source_text, node) or "<call>"
            self.violations.append(
                f"{self.source.as_posix()}:{node.lineno}:{self.test_id}:"
                f"unresolved write target:{expression}"
            )
            return
        relative = fact.relative
        known_prefix = fact.known_prefix
        matches = {
            key
            for key in self.allowed
            if key[0] == self.suite_id
            and key[1] == self.source.as_posix()
            and key[2] == self.test_id
            and (
                (
                    relative is not None
                    and (
                        relative == key[3]
                        or relative.startswith(f"{key[3]}/")
                    )
                )
                or (
                    relative is None
                    and known_prefix is not None
                    and (
                        known_prefix == key[3]
                        or known_prefix.startswith(f"{key[3]}/")
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
        fact = self._fact(node.value) or self._unknown()
        for target in node.targets:
            self._bind_assignment(target, fact)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            fact = self._fact(node.value) or self._unknown()
            key = _target_key(node.target)
            if key is not None and key != "PROJECT_ROOT":
                self.environment[key] = fact
            self.visit(node.value)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for imported in node.names:
            local_name = imported.asname or imported.name
            self.import_aliases[local_name] = f"{module}.{imported.name}"
            self.environment[local_name] = self._unknown()

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            local_name = imported.asname or imported.name.split(".", 1)[0]
            self.import_aliases[local_name] = imported.name
            self.environment[local_name] = self._unknown()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not node.name.startswith("test_"):
            self.helpers[node.name] = node
            return
        previous_environment = self.environment
        previous_import_aliases = self.import_aliases
        previous_test_id = self.test_id
        self.environment = dict(previous_environment)
        self.import_aliases = dict(previous_import_aliases)
        if node.name.startswith("test_"):
            class_name = getattr(node, "_contract_class_name", None)
            self.test_id = (
                f"{self.source.stem}.{class_name}.{node.name}"
                if class_name is not None
                else f"{self.source.stem}.{node.name}"
            )
            for parameter in node.args.args:
                self.environment[parameter.arg] = (
                    self._opaque()
                    if parameter.arg in {"self", "cls"}
                    else self._unknown()
                )
        for statement in node.body:
            self.visit(statement)
        self.environment = previous_environment
        self.import_aliases = previous_import_aliases
        self.test_id = previous_test_id

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous_environment = self.environment
        for statement in node.body:
            if (
                isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not statement.name.startswith("test_")
            ):
                self.helpers[statement.name] = statement
        fixture_environment = dict(previous_environment)
        setup = next(
            (
                statement
                for statement in node.body
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and statement.name == "setUp"
            ),
            None,
        )
        if setup is not None:
            self.environment = dict(previous_environment)
            self.environment["self"] = self._opaque()
            for statement in setup.body:
                self.visit(statement)
            fixture_environment.update(
                {
                    key: fact
                    for key, fact in self.environment.items()
                    if key != "self"
                }
            )
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                setattr(statement, "_contract_class_name", node.name)
                if statement.name.startswith("test_"):
                    self.environment = dict(fixture_environment)
                else:
                    self.environment = dict(previous_environment)
            self.visit(statement)
        self.environment = previous_environment

    def visit_For(self, node: ast.For) -> None:
        self._bind_assignment(
            node.target,
            self._fact(node.iter) or self._unknown(),
        )
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind_assignment(
                    item.optional_vars,
                    self._fact(item.context_expr) or self._unknown(),
                )
            self.visit(item.context_expr)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_Call(self, node: ast.Call) -> None:
        canonical = self._canonical_name(node.func)
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver_helper = (
                method in self.helpers
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"self", "cls"}
            )
            if (
                receiver_helper
                and method not in self.active_helpers
                and id(node) not in self.evaluated_helper_calls
            ):
                self._helper_return_fact(method, node)
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
            elif (
                method in {"rename", "replace"}
                and len(node.args) == 1
                and canonical not in {"os.rename", "os.replace"}
            ):
                receiver = self._fact(node.func.value)
                if (
                    receiver is not None
                    and receiver.value_kind in {
                        "absolute_string",
                        "bytes",
                        "path_string",
                        "relative_string",
                    }
                ):
                    pass
                elif (
                    receiver is None
                    or receiver.rooted_in_project is not None
                    or receiver.unresolved_local
                ):
                    self._record(node, receiver)
                    self._record(node, self._fact(node.args[0]))
            elif (
                canonical in _TWO_PATH_MUTATOR_CALLS
                and len(node.args) >= 2
            ):
                self._record(node, self._fact(node.args[1]))
            elif canonical in _FUNCTION_PATH_MUTATOR_CALLS and node.args:
                self._record(node, self._fact(node.args[0]))
            elif canonical in _DATABASE_CONNECTORS and node.args:
                self._record(node, self._fact(node.args[0]))
            elif method in _OUTPUT_METHODS and node.args:
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
            helper = (
                self.helpers.get(node.func.id)
                if node.func.id not in self.import_aliases
                else None
            )
            if helper is not None and node.func.id not in self.active_helpers:
                previous_environment = self.environment
                previous_import_aliases = self.import_aliases
                self.environment = {
                    **previous_environment,
                    **self._bind_helper(helper, node),
                }
                self.import_aliases = dict(previous_import_aliases)
                self.active_helpers.add(node.func.id)
                try:
                    for statement in helper.body:
                        self.visit(statement)
                finally:
                    self.active_helpers.remove(node.func.id)
                    self.environment = previous_environment
                    self.import_aliases = previous_import_aliases
            assert canonical is not None
            operation = canonical.rsplit(".", 1)[-1]
            if (
                canonical in _TWO_PATH_MUTATOR_CALLS
                and len(node.args) >= 2
            ):
                self._record(node, self._fact(node.args[1]))
            elif canonical in _FUNCTION_PATH_MUTATOR_CALLS and node.args:
                self._record(node, self._fact(node.args[0]))
            elif canonical in _DATABASE_CONNECTORS and node.args:
                self._record(node, self._fact(node.args[0]))
            elif operation == "open" and node.args:
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
            elif operation in _TEMPORARY_CREATORS:
                directory = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "dir"
                    ),
                    None,
                )
                self._record(node, self._fact(directory) if directory else None)
            elif (
                helper is None
                and operation.startswith(("write", "create", "copy", "move"))
                and node.args
            ):
                self._record(node, self._fact(node.args[0]))
        if canonical in _SUBPROCESS_CALLS:
            cwd = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "cwd"
                ),
                None,
            )
            if cwd is not None:
                cwd_fact = self._fact(cwd)
                if (
                    cwd_fact is None
                    or cwd_fact.rooted_in_project is None
                    or cwd_fact.parts
                ):
                    self._record(node, cwd_fact)
            if node.args:
                command = _sequence_elements(node.args[0])
                for index, argument in enumerate(command[:-1]):
                    if (
                        isinstance(argument, ast.Constant)
                        and argument.value in _SUBPROCESS_OUTPUT_FLAGS
                    ):
                        self._record(node, self._fact(command[index + 1]))
        self.generic_visit(node)


def analyze_source(
    source_text: str,
    source: Path,
    allowed: dict[tuple[str, str, str, str], str],
    *,
    suite_id: str = "video-workflow",
) -> tuple[list[str], set[tuple[str, str, str, str]]]:
    tree = ast.parse(source_text, filename=str(source))
    analyzer = _LocalWriteAnalyzer(
        source_text,
        source,
        allowed,
        suite_id,
    )
    analyzer.prepare(tree)
    for statement in tree.body:
        analyzer.visit(statement)
    return analyzer.violations, analyzer.used


@dataclass(frozen=True)
class _WorkflowUuidPart:
    max_length: int | None

    @property
    def could_be_uuid32(self) -> bool:
        return self.max_length is None or self.max_length >= 32


@dataclass(frozen=True)
class _WorkflowWorkspaceFact:
    module_rooted: bool
    parts: tuple[str | None | _WorkflowUuidPart, ...] = ()


_UNKNOWN_WORKFLOW_FACT = _WorkflowWorkspaceFact(False)
_AMBIGUOUS_IMPORT = "<ambiguous-import>"


@dataclass
class _WorkflowLexicalScope:
    parent: _WorkflowLexicalScope | None
    bindings: dict[str, _WorkflowWorkspaceFact]
    import_aliases: dict[str, str | None]
    local_names: set[str]
    helpers: dict[str, list[_WorkflowHelper]]


@dataclass(frozen=True, eq=False)
class _WorkflowHelper:
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
    defining_scope: _WorkflowLexicalScope
    class_name: str | None = None
    implicit_receiver: bool = False


class _WorkflowWorkspaceBypassAnalyzer(ast.NodeVisitor):
    """Find workspace paths built from a module root instead of its helper."""

    def __init__(self) -> None:
        self.scope = _WorkflowLexicalScope(None, {}, {}, set(), {})
        self.method_helpers: dict[str, _WorkflowHelper] = {}
        self.method_candidates: dict[str, list[_WorkflowHelper]] = {}
        self.active_helpers: set[int] = set()
        self.helper_evaluation_depth = 0
        self.return_fact_stack: list[list[_WorkflowWorkspaceFact]] = []
        self.current_class: str | None = None
        self.violations: set[int] = set()

    def prepare(self, tree: ast.Module) -> None:
        self.scope = self._make_scope(tree.body, None)
        for statement in tree.body:
            if not isinstance(statement, ast.ClassDef):
                continue
            for member in statement.body:
                if not isinstance(
                    member,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                helper = _WorkflowHelper(
                    member,
                    self.scope,
                    statement.name,
                    not any(
                        isinstance(decorator, ast.Name)
                        and decorator.id == "staticmethod"
                        for decorator in member.decorator_list
                    ),
                )
                qualified_name = f"{statement.name}.{member.name}"
                self.method_helpers[qualified_name] = helper
                self.method_candidates.setdefault(member.name, []).append(
                    helper
                )

    @staticmethod
    def _assigned_names(target: ast.AST) -> set[str]:
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.List, ast.Tuple)):
            return {
                name
                for element in target.elts
                for name in _WorkflowWorkspaceBypassAnalyzer._assigned_names(
                    element
                )
            }
        return set()

    def _scope_declarations(
        self,
        statements: list[ast.stmt],
    ) -> tuple[set[str], list[tuple[str, ast.AST]]]:
        names: set[str] = set()
        helpers: list[tuple[str, ast.AST]] = []

        def collect(statement: ast.stmt) -> None:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(statement.name)
                helpers.append((statement.name, statement))
                return
            if isinstance(statement, ast.ClassDef):
                names.add(statement.name)
                return
            if isinstance(statement, ast.Import):
                names.update(
                    imported.asname or imported.name.split(".", 1)[0]
                    for imported in statement.names
                )
                return
            if isinstance(statement, ast.ImportFrom):
                names.update(
                    imported.asname or imported.name
                    for imported in statement.names
                )
                return
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    assigned = self._assigned_names(target)
                    names.update(assigned)
                    if isinstance(statement.value, ast.Lambda):
                        helpers.extend(
                            (name, statement.value) for name in assigned
                        )
            elif isinstance(statement, ast.AnnAssign):
                assigned = self._assigned_names(statement.target)
                names.update(assigned)
                if isinstance(statement.value, ast.Lambda):
                    helpers.extend((name, statement.value) for name in assigned)
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                names.update(self._assigned_names(statement.target))
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    if item.optional_vars is not None:
                        names.update(self._assigned_names(item.optional_vars))
            elif isinstance(statement, ast.ExceptHandler):
                if statement.name:
                    names.add(statement.name)

            nested_bodies: list[list[ast.stmt]] = []
            for field_name in ("body", "orelse", "finalbody"):
                value = getattr(statement, field_name, None)
                if isinstance(value, list):
                    nested_bodies.append(value)
            if isinstance(statement, ast.Try):
                nested_bodies.extend(
                    handler.body for handler in statement.handlers
                )
            if isinstance(statement, ast.Match):
                nested_bodies.extend(case.body for case in statement.cases)
            for body in nested_bodies:
                for child in body:
                    collect(child)

        for statement in statements:
            collect(statement)
        return names, helpers

    def _make_scope(
        self,
        statements: list[ast.stmt],
        parent: _WorkflowLexicalScope | None,
        *,
        parameters: ast.arguments | None = None,
    ) -> _WorkflowLexicalScope:
        names, helper_nodes = self._scope_declarations(statements)
        if parameters is not None:
            names.update(
                argument.arg
                for argument in (
                    *parameters.posonlyargs,
                    *parameters.args,
                    *parameters.kwonlyargs,
                )
            )
            if parameters.vararg is not None:
                names.add(parameters.vararg.arg)
            if parameters.kwarg is not None:
                names.add(parameters.kwarg.arg)
        scope = _WorkflowLexicalScope(
            parent,
            {name: _UNKNOWN_WORKFLOW_FACT for name in names},
            {name: None for name in names},
            names,
            {},
        )
        for name, node in helper_nodes:
            scope.helpers.setdefault(name, []).append(
                _WorkflowHelper(node, scope)
            )
        return scope

    def _lookup_binding(
        self,
        key: str,
    ) -> _WorkflowWorkspaceFact | None:
        scope: _WorkflowLexicalScope | None = self.scope
        while scope is not None:
            if key in scope.bindings:
                return scope.bindings[key]
            root_name = key.split(".", 1)[0]
            if root_name in scope.local_names:
                return _UNKNOWN_WORKFLOW_FACT
            scope = scope.parent
        return None

    def _canonical_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            scope: _WorkflowLexicalScope | None = self.scope
            while scope is not None:
                if node.id in scope.import_aliases:
                    return scope.import_aliases[node.id]
                scope = scope.parent
            return node.id
        if isinstance(node, ast.Attribute):
            owner = self._canonical_name(node.value)
            if owner is not None:
                return f"{owner}.{node.attr}"
        return None

    def _is_uuid_hex(self, node: ast.AST) -> bool:
        canonical = (
            self._canonical_name(node.value.func)
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Call)
            )
            else None
        )
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "hex"
            and isinstance(node.value, ast.Call)
            and (
                canonical == "uuid.uuid4"
                or (
                    canonical is not None
                    and canonical.startswith(f"{_AMBIGUOUS_IMPORT}.")
                )
            )
        )

    @staticmethod
    def _only_uuid_part(
        fact: _WorkflowWorkspaceFact,
    ) -> _WorkflowUuidPart | None:
        if (
            not fact.module_rooted
            and len(fact.parts) == 1
            and isinstance(fact.parts[0], _WorkflowUuidPart)
        ):
            return fact.parts[0]
        return None

    @staticmethod
    def _slice_uuid_part(
        part: _WorkflowUuidPart,
        subscript: ast.AST,
    ) -> _WorkflowUuidPart:
        if isinstance(subscript, ast.Constant) and isinstance(
            subscript.value,
            int,
        ):
            return _WorkflowUuidPart(1)
        if not isinstance(subscript, ast.Slice) or part.max_length is None:
            return part

        bounds: list[int | None] = []
        for bound in (subscript.lower, subscript.upper, subscript.step):
            if bound is None:
                bounds.append(None)
            elif isinstance(bound, ast.Constant) and isinstance(
                bound.value,
                int,
            ):
                bounds.append(bound.value)
            else:
                return part
        lower, upper, step = bounds
        if step == 0:
            return part
        start, stop, stride = slice(lower, upper, step).indices(
            part.max_length
        )
        return _WorkflowUuidPart(len(range(start, stop, stride)))

    def _helper(
        self,
        node: ast.Call,
    ) -> _WorkflowHelper | list[_WorkflowHelper] | str | None:
        if isinstance(node.func, ast.Name):
            scope: _WorkflowLexicalScope | None = self.scope
            while scope is not None:
                candidates = scope.helpers.get(node.func.id, [])
                if len(candidates) == 1:
                    return candidates[0]
                if len(candidates) > 1:
                    return candidates
                if node.func.id in scope.local_names:
                    return None
                scope = scope.parent
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"self", "cls"}
        ):
            if self.current_class is not None:
                qualified_name = f"{self.current_class}.{node.func.attr}"
                if qualified_name in self.method_helpers:
                    return self.method_helpers[qualified_name]
            candidates = self.method_candidates.get(node.func.attr, [])
            if candidates:
                return "<ambiguous-helper>"
        return None

    @staticmethod
    def _literal_parts(value: str) -> tuple[str, ...]:
        return tuple(
            part
            for part in re.split(r"[\\/]+", value)
            if part
        )

    def _fact(self, node: ast.AST) -> _WorkflowWorkspaceFact:
        key = _target_key(node)
        if key is not None:
            binding = self._lookup_binding(key)
            if binding is not None:
                return binding
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _WorkflowWorkspaceFact(
                False,
                self._literal_parts(node.value),
            )
        if isinstance(node, ast.JoinedStr):
            parts: list[str | None | _WorkflowUuidPart] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(
                    value.value,
                    str,
                ):
                    parts.extend(self._literal_parts(value.value))
                elif isinstance(value, ast.FormattedValue):
                    embedded = self._fact(value.value)
                    uuid_part = self._only_uuid_part(embedded)
                    parts.append(uuid_part if uuid_part is not None else None)
            return _WorkflowWorkspaceFact(False, tuple(parts) or (None,))
        if self._is_uuid_hex(node):
            return _WorkflowWorkspaceFact(False, (_WorkflowUuidPart(32),))
        if isinstance(node, ast.Subscript):
            base = self._fact(node.value)
            uuid_part = self._only_uuid_part(base)
            if uuid_part is not None:
                return _WorkflowWorkspaceFact(
                    False,
                    (self._slice_uuid_part(uuid_part, node.slice),),
                )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self._fact(node.left)
            right = self._fact(node.right)
            return _WorkflowWorkspaceFact(
                left.module_rooted,
                left.parts + right.parts,
            )
        if isinstance(node, ast.Call):
            canonical = self._canonical_name(node.func)
            if (
                canonical
                == "tests.video_workflow._test_run.module_test_root"
            ):
                return _WorkflowWorkspaceFact(True)
            helper = self._helper(node)
            if helper == "<ambiguous-helper>":
                return _WorkflowWorkspaceFact(
                    True,
                    (_WorkflowUuidPart(None),),
                )
            if isinstance(helper, list):
                return self._merge_facts(
                    [
                        self._helper_return_fact(candidate, node)
                        for candidate in helper
                    ]
                )
            if isinstance(helper, _WorkflowHelper):
                return self._helper_return_fact(helper, node)
        return _WorkflowWorkspaceFact(False)

    def _bind(
        self,
        target: ast.AST,
        fact: _WorkflowWorkspaceFact,
        *,
        preserve_helper: ast.Lambda | None = None,
    ) -> None:
        key = _target_key(target)
        if key is None:
            return
        self.scope.bindings[key] = fact
        root_name = key.split(".", 1)[0]
        if root_name in self.scope.local_names:
            self.scope.import_aliases[root_name] = None
            if preserve_helper is None:
                self.scope.helpers.pop(root_name, None)
            else:
                self.scope.helpers[root_name] = [
                    _WorkflowHelper(preserve_helper, self.scope)
                ]

    def _apply_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for imported in node.names:
                name = imported.asname or imported.name.split(".", 1)[0]
                self.scope.import_aliases[name] = imported.name
        else:
            module = node.module or ""
            for imported in node.names:
                name = imported.asname or imported.name
                self.scope.import_aliases[name] = f"{module}.{imported.name}"

    def _helper_return_fact(
        self,
        helper: _WorkflowHelper,
        call: ast.Call,
    ) -> _WorkflowWorkspaceFact:
        helper_key = id(helper.node)
        if helper_key in self.active_helpers:
            return _WorkflowWorkspaceFact(False)
        node = helper.node
        if isinstance(node, ast.Lambda):
            body: list[ast.stmt] = []
            parameters = node.args
        else:
            body = node.body
            parameters = node.args
        helper_scope = self._make_scope(
            body,
            helper.defining_scope,
            parameters=parameters,
        )
        all_positional_parameters = (
            *parameters.posonlyargs,
            *parameters.args,
        )
        positional_parameters = (
            all_positional_parameters[1:]
            if helper.implicit_receiver
            else all_positional_parameters
        )
        caller_argument_facts = [self._fact(argument) for argument in call.args]
        caller_keyword_facts = {
            keyword.arg: self._fact(keyword.value)
            for keyword in call.keywords
            if keyword.arg is not None
        }
        previous_scope = self.scope
        self.scope = helper.defining_scope
        try:
            positional_defaults = {
                parameter.arg: self._fact(default)
                for parameter, default in zip(
                    all_positional_parameters[-len(parameters.defaults) :],
                    parameters.defaults,
                )
            }
            keyword_defaults = {
                parameter.arg: self._fact(default)
                for parameter, default in zip(
                    parameters.kwonlyargs,
                    parameters.kw_defaults,
                )
                if default is not None
            }
        finally:
            self.scope = previous_scope
        unresolved_argument = _WorkflowWorkspaceFact(
            False,
            (_WorkflowUuidPart(None),),
        )
        if helper.implicit_receiver and all_positional_parameters:
            helper_scope.bindings[
                all_positional_parameters[0].arg
            ] = _UNKNOWN_WORKFLOW_FACT
        for index, parameter in enumerate(positional_parameters):
            if index < len(caller_argument_facts):
                helper_scope.bindings[parameter.arg] = caller_argument_facts[index]
            elif parameter.arg in caller_keyword_facts:
                helper_scope.bindings[parameter.arg] = caller_keyword_facts[
                    parameter.arg
                ]
            else:
                helper_scope.bindings[parameter.arg] = positional_defaults.get(
                    parameter.arg,
                    unresolved_argument,
                )
        for parameter in parameters.kwonlyargs:
            if parameter.arg in caller_keyword_facts:
                helper_scope.bindings[parameter.arg] = caller_keyword_facts[
                    parameter.arg
                ]
            else:
                helper_scope.bindings[parameter.arg] = keyword_defaults.get(
                    parameter.arg,
                    unresolved_argument,
                )
        if parameters.vararg is not None:
            helper_scope.bindings[parameters.vararg.arg] = unresolved_argument
        if parameters.kwarg is not None:
            helper_scope.bindings[parameters.kwarg.arg] = unresolved_argument
        previous_class = self.current_class
        self.scope = helper_scope
        if helper.class_name is not None:
            self.current_class = helper.class_name
        self.active_helpers.add(helper_key)
        self.helper_evaluation_depth += 1
        self.return_fact_stack.append([])
        try:
            if isinstance(node, ast.Lambda):
                return self._fact(node.body)
            for statement in body:
                self.visit(statement)
            return self._merge_facts(self.return_fact_stack[-1])
        finally:
            self.return_fact_stack.pop()
            self.helper_evaluation_depth -= 1
            self.active_helpers.remove(helper_key)
            self.current_class = previous_class
            self.scope = previous_scope

    @staticmethod
    def _merge_facts(
        facts: list[_WorkflowWorkspaceFact],
    ) -> _WorkflowWorkspaceFact:
        if not facts:
            return _UNKNOWN_WORKFLOW_FACT
        if all(fact == facts[0] for fact in facts[1:]):
            return facts[0]
        module_rooted = any(fact.module_rooted for fact in facts)
        if len({len(fact.parts) for fact in facts}) != 1:
            if any(
                isinstance(part, _WorkflowUuidPart)
                for fact in facts
                for part in fact.parts
            ):
                return _WorkflowWorkspaceFact(
                    module_rooted,
                    (_WorkflowUuidPart(None),),
                )
            return _WorkflowWorkspaceFact(module_rooted)
        merged_parts: list[str | None | _WorkflowUuidPart] = []
        for parts in zip(*(fact.parts for fact in facts)):
            if all(part == parts[0] for part in parts[1:]):
                merged_parts.append(parts[0])
            elif any(isinstance(part, _WorkflowUuidPart) for part in parts):
                lengths = [
                    part.max_length
                    for part in parts
                    if isinstance(part, _WorkflowUuidPart)
                ]
                merged_parts.append(
                    _WorkflowUuidPart(
                        None if None in lengths else max(lengths)
                    )
                )
            else:
                merged_parts.append(None)
        return _WorkflowWorkspaceFact(module_rooted, tuple(merged_parts))

    def _visit_branch(
        self,
        statements: list[ast.stmt],
        base_scope: _WorkflowLexicalScope,
    ) -> _WorkflowLexicalScope:
        self.scope = _WorkflowLexicalScope(
            base_scope.parent,
            dict(base_scope.bindings),
            dict(base_scope.import_aliases),
            set(base_scope.local_names),
            {
                name: list(helpers)
                for name, helpers in base_scope.helpers.items()
            },
        )
        for statement in statements:
            self.visit(statement)
        return self.scope

    def _merge_branch_scopes(
        self,
        base_scope: _WorkflowLexicalScope,
        branches: list[_WorkflowLexicalScope],
    ) -> None:
        all_keys = set().union(
            *(branch.bindings.keys() for branch in branches)
        )
        base_scope.bindings = {
            key: self._merge_facts(
                [
                    branch.bindings.get(key, _UNKNOWN_WORKFLOW_FACT)
                    for branch in branches
                ]
            )
            for key in all_keys
        }
        alias_names = set().union(
            *(branch.import_aliases.keys() for branch in branches)
        )
        base_scope.import_aliases = {
            name: (
                values[0]
                if all(value == values[0] for value in values[1:])
                else _AMBIGUOUS_IMPORT
            )
            for name in alias_names
            for values in [
                [branch.import_aliases.get(name) for branch in branches]
            ]
        }
        helper_names = set().union(
            *(branch.helpers.keys() for branch in branches)
        )
        merged_helpers: dict[str, list[_WorkflowHelper]] = {}
        for name in helper_names:
            values = [branch.helpers.get(name, []) for branch in branches]
            if all(value == values[0] for value in values[1:]):
                merged_helpers[name] = values[0]
                continue
            unique = list(
                {
                    id(helper): helper
                    for value in values
                    for helper in value
                }.values()
            )
            if len(unique) == 1:
                unique.append(unique[0])
            merged_helpers[name] = unique
        base_scope.helpers = merged_helpers
        self.scope = base_scope

    @staticmethod
    def _is_workspace(fact: _WorkflowWorkspaceFact) -> bool:
        if not fact.module_rooted:
            return False
        for index, part in enumerate(fact.parts):
            if (
                isinstance(part, _WorkflowUuidPart)
                and part.could_be_uuid32
                and any(
                    trailing in {"w", "workspace"}
                    for trailing in fact.parts[index + 1 :]
                )
            ):
                return True
        return False

    def visit_Assign(self, node: ast.Assign) -> None:
        fact = self._fact(node.value)
        for target in node.targets:
            self._bind(
                target,
                fact,
                preserve_helper=(
                    node.value if isinstance(node.value, ast.Lambda) else None
                ),
            )
        if (
            self.helper_evaluation_depth == 0
            and self._is_workspace(fact)
        ):
            self.violations.add(node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            fact = self._fact(node.value)
            self._bind(
                node.target,
                fact,
                preserve_helper=(
                    node.value if isinstance(node.value, ast.Lambda) else None
                ),
            )
            if (
                self.helper_evaluation_depth == 0
                and self._is_workspace(fact)
            ):
                self.violations.add(node.lineno)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self._apply_import(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._apply_import(node)

    def visit_Call(self, node: ast.Call) -> None:
        for argument in (*node.args, *(item.value for item in node.keywords)):
            if (
                self.helper_evaluation_depth == 0
                and self._is_workspace(self._fact(argument))
            ):
                self.violations.add(node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if self.return_fact_stack and node.value is not None:
            self.return_fact_stack[-1].append(self._fact(node.value))

    def visit_If(self, node: ast.If) -> None:
        base_scope = self.scope
        branches = [
            self._visit_branch(node.body, base_scope),
            self._visit_branch(node.orelse, base_scope),
        ]
        self._merge_branch_scopes(base_scope, branches)

    def visit_Try(self, node: ast.Try) -> None:
        base_scope = self.scope
        branches = [
            self._visit_branch([*node.body, *node.orelse], base_scope),
            *(
                self._visit_branch(handler.body, base_scope)
                for handler in node.handlers
            ),
        ]
        self._merge_branch_scopes(base_scope, branches)
        for statement in node.finalbody:
            self.visit(statement)

    visit_TryStar = visit_Try

    def visit_Match(self, node: ast.Match) -> None:
        base_scope = self.scope
        branches = [
            self._visit_branch([], base_scope),
            *(
                self._visit_branch(case.body, base_scope)
                for case in node.cases
            ),
        ]
        self._merge_branch_scopes(base_scope, branches)

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        base_scope = self.scope
        branches = [
            self._visit_branch([], base_scope),
            self._visit_branch(node.body, base_scope),
            self._visit_branch(node.orelse, base_scope),
        ]
        self._merge_branch_scopes(base_scope, branches)

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.helper_evaluation_depth:
            return
        if not node.name.startswith("test_"):
            return
        previous_scope = self.scope
        self.scope = self._make_scope(
            node.body,
            previous_scope,
            parameters=node.args,
        )
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope = previous_scope

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous_class = self.current_class
        self.current_class = node.name
        try:
            for statement in node.body:
                if (
                    isinstance(
                        statement,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                    and statement.name.startswith("test_")
                ):
                    self.visit(statement)
        finally:
            self.current_class = previous_class


def workflow_workspace_bypasses(source_text: str, source: Path) -> list[str]:
    tree = ast.parse(source_text, filename=str(source))
    analyzer = _WorkflowWorkspaceBypassAnalyzer()
    analyzer.prepare(tree)
    analyzer.visit(tree)
    return [
        f"{source.as_posix()}:{line}: workflow workspace must use "
        "new_workflow_workspace"
        for line in sorted(analyzer.violations)
    ]


def registered_contract_sources() -> dict[str, str]:
    registry = load_registry(PROJECT_ROOT, REGISTRY_PATH)
    sources: dict[str, str] = {}
    for suite in registry.suites:
        for source in registry.registered_test_files([suite.suite_id]):
            if source in sources:
                raise AssertionError(f"registry source belongs to two suites: {source}")
            sources[source] = suite.suite_id
    own_source = Path(__file__).resolve().relative_to(PROJECT_ROOT).as_posix()
    sources.pop(own_source)
    return sources


def load_local_write_exceptions(
    source_suites: dict[str, str],
) -> dict[tuple[str, str, str, str], str]:
    document = json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))
    return parse_local_write_exceptions(document, source_suites)


def parse_local_write_exceptions(
    document: object,
    source_suites: dict[str, str],
) -> dict[tuple[str, str, str, str], str]:
    if not isinstance(document, dict):
        raise AssertionError("exception registry must be an object")
    if set(document) != {"schema_name", "schema_version", "exceptions"}:
        raise AssertionError("exception registry fields are invalid")
    if document["schema_name"] != "video2pdf.test-local-write-exceptions":
        raise AssertionError("exception registry schema_name is invalid")
    if document["schema_version"] != 1:
        raise AssertionError("exception registry schema_version is invalid")
    if not isinstance(document["exceptions"], list):
        raise AssertionError("exception registry exceptions must be a list")

    allowed: dict[tuple[str, str, str, str], str] = {}
    expected_fields = {
        "suite_id",
        "source_path",
        "test_id",
        "path_prefix",
        "reason",
    }
    for item in document["exceptions"]:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise AssertionError("exception entry fields are invalid")
        suite_id = item["suite_id"]
        source_path = item["source_path"]
        test_id = item["test_id"]
        prefix = item["path_prefix"]
        reason = item["reason"]
        if (
            not all(
                isinstance(value, str) and value.strip()
                for value in (suite_id, source_path, test_id, prefix, reason)
            )
            or source_suites.get(source_path) != suite_id
        ):
            raise AssertionError(
                "exception suite and source must identify one registered source"
            )
        source = PurePosixPath(source_path)
        if (
            source.is_absolute()
            or source.as_posix() != source_path
            or ".." in source.parts
            or source.suffix != ".py"
        ):
            raise AssertionError("exception source_path is invalid")
        if (
            not test_id.startswith(f"{source.stem}.")
            or re.fullmatch(
                r"test_[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+",
                test_id,
            )
            is None
        ):
            raise AssertionError("exception test_id is not exact for its source")
        prefix_path = PurePosixPath(prefix)
        if (
            prefix_path.is_absolute()
            or prefix_path.as_posix() != prefix
            or ".." in prefix_path.parts
            or "*" in prefix
            or len(prefix_path.parts) < 3
        ):
            raise AssertionError("exception path_prefix is not narrow")
        key = (suite_id, source_path, test_id, prefix)
        if key in allowed:
            raise AssertionError("duplicate local-write exception")
        allowed[key] = reason
    return allowed


class RepositoryGeneratedPathContractTests(unittest.TestCase):
    def test_registry_is_the_only_source_inventory(self) -> None:
        registry = load_registry(PROJECT_ROOT, REGISTRY_PATH)
        registered = registry.registered_test_files()

        self.assertEqual(5, len(registry.suites))
        self.assertEqual(69, len(registered))
        self.assertEqual(68, len(registered_contract_sources()))
        self.assertFalse(
            any(path.startswith(".claude/skills/") for path in registered)
        )
        self.assertIn(
            ".agents/skills/final-delivery-acceptance/scripts/"
            "test_delivery_guard.py",
            registered,
        )

    def test_exception_registry_rejects_cross_source_stale_duplicate_and_wide(
        self,
    ) -> None:
        source_suites = registered_contract_sources()
        valid = json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))

        for mutate in (
            lambda item: item.__setitem__("suite_id", "skill-tests"),
            lambda item: item.__setitem__(
                "source_path",
                "tests/video_workflow/test_missing.py",
            ),
            lambda item: item.__setitem__(
                "test_id",
                "test_other.Case.test_method",
            ),
            lambda item: item.__setitem__("path_prefix", "workspace"),
        ):
            document = json.loads(json.dumps(valid))
            mutate(document["exceptions"][0])
            with self.subTest(document=document), self.assertRaises(
                AssertionError
            ):
                parse_local_write_exceptions(document, source_suites)

        duplicate = json.loads(json.dumps(valid))
        duplicate["exceptions"].append(dict(duplicate["exceptions"][0]))
        with self.assertRaises(AssertionError):
            parse_local_write_exceptions(duplicate, source_suites)

    def test_analysis_rejects_imported_helper_and_unknown_imported_returns(
        self,
    ) -> None:
        source = """
from helper import repo_destination, write_result as emit

def test_escape():
    repo_destination().write_text("{}")
    emit(repo_destination())
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 2)

    def test_analysis_allows_only_explicit_external_boundary_providers(
        self,
    ) -> None:
        source = """
from tests.project_test_runner._fixture_root import new_fixture_dir
from tests.video_workflow._test_run import module_test_root, new_case_dir

def test_external_boundaries():
    new_fixture_dir("case").write_text("{}")
    module_test_root(PROJECT_ROOT).mkdir()
    new_case_dir("test.id", label="case").write_bytes(b"{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_workflow_workspace_contract_is_scoped_to_path_bypasses(
        self,
    ) -> None:
        source = """
import uuid
from uuid import uuid4
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def new_test_root(label):
    return TEST_RUNS / f"{label}-{uuid.uuid4().hex}"

def test_paths():
    quarantine = TEST_RUNS / "quarantine"
    quarantine.mkdir()
    start_kernel(new_test_root("semantic-label") / "workspace")
    start_kernel(TEST_RUNS / uuid.uuid4().hex / "workspace")
    start_kernel(TEST_RUNS / uuid4().hex / "workspace")
    start_kernel(TEST_RUNS / f"{uuid.uuid4().hex}" / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 4)
        self.assertTrue(all("new_workflow_workspace" in item for item in violations))

    def test_workflow_workspace_contract_follows_method_thin_helpers(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

class Harness:
    def new_test_root(self):
        return TEST_RUNS / uuid.uuid4().hex

    def test_paths(self):
        start_kernel(self.new_test_root() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 1)

    def test_workflow_workspace_contract_qualifies_same_named_method_helpers(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

class SafeHarness:
    def new_test_root(self):
        return TEST_RUNS / uuid.uuid4().hex[:8]

    def test_paths(self):
        start_kernel(self.new_test_root() / "workspace")

class UnsafeHarness:
    def new_test_root(self):
        return TEST_RUNS / uuid.uuid4().hex

    def test_paths(self):
        start_kernel(self.new_test_root() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 1)
        self.assertIn(":19:", violations[0])

    def test_workflow_workspace_contract_fails_closed_for_ambiguous_methods(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

class FirstHarness:
    def new_test_root(self):
        return TEST_RUNS / uuid.uuid4().hex[:8]

class SecondHarness:
    def new_test_root(self):
        return TEST_RUNS / uuid.uuid4().hex

class RuntimeSelectedHarness(choose_base()):
    def test_paths(self):
        start_kernel(self.new_test_root() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 1)
        self.assertIn(":17:", violations[0])

    def test_workflow_workspace_contract_fails_closed_for_unknown_base_method(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

class UnrelatedHarness:
    def new_test_root(self):
        return TEST_RUNS / uuid.uuid4().hex[:8]

class RuntimeSelectedHarness(choose_base()):
    def test_paths(self):
        start_kernel(self.new_test_root() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 1)
        self.assertIn(":13:", violations[0])

    def test_workflow_workspace_contract_keeps_unproven_uuid_slices_risky(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def test_paths(stop):
    start_kernel(TEST_RUNS / uuid.uuid4().hex[:] / "workspace")
    start_kernel(TEST_RUNS / uuid.uuid4().hex[:32] / "workspace")
    start_kernel(TEST_RUNS / uuid.uuid4().hex[0:32] / "workspace")
    start_kernel(TEST_RUNS / uuid.uuid4().hex[:stop] / "workspace")
    start_kernel(TEST_RUNS / uuid.uuid4().hex[:8] / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 4)

    def test_workflow_workspace_contract_propagates_uuid_aliases_and_helpers(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def full_token():
    return uuid.uuid4().hex[:]

def compact_token():
    return uuid.uuid4().hex[:8]

def test_paths():
    token = uuid.uuid4().hex
    start_kernel(TEST_RUNS / token[:] / "workspace")
    start_kernel(TEST_RUNS / full_token() / "workspace")
    start_kernel(TEST_RUNS / compact_token() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 2)

    def test_workflow_workspace_contract_resolves_function_lexical_scopes(
        self,
    ) -> None:
        source = """
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def module_token():
    return "module-token"

def test_paths():
    import uuid
    from uuid import uuid4 as local_uuid4

    def nested_full():
        return uuid.uuid4().hex

    nested_compact = lambda: local_uuid4().hex[:8]

    def closure():
        def module_token():
            return local_uuid4().hex
        return module_token()

    start_kernel(TEST_RUNS / uuid.uuid4().hex / "workspace")
    start_kernel(TEST_RUNS / local_uuid4().hex / "workspace")
    start_kernel(TEST_RUNS / nested_full() / "workspace")
    start_kernel(TEST_RUNS / nested_compact() / "workspace")
    start_kernel(TEST_RUNS / closure() / "workspace")

def test_shadowed_import():
    import uuid
    uuid = application_uuid_provider
    start_kernel(TEST_RUNS / uuid.uuid4().hex / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 4)
        self.assertEqual(
            [item.rsplit(":", 2)[-2] for item in violations],
            ["23", "24", "25", "27"],
        )

    def test_workflow_workspace_contract_fails_closed_for_ambiguous_helpers(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def test_paths(condition):
    if condition:
        def token():
            return uuid.uuid4().hex[:8]
    else:
        def token():
            return uuid.uuid4().hex
    start_kernel(TEST_RUNS / token() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 1)
        self.assertIn(":14:", violations[0])

    def test_workflow_workspace_contract_follows_nested_helpers_in_branches(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def test_paths(condition, items, value):
    if condition:
        def if_token():
            return uuid.uuid4().hex
    else:
        def if_token():
            return uuid.uuid4().hex
    start_kernel(TEST_RUNS / if_token() / "workspace")

    try:
        def try_token():
            return uuid.uuid4().hex
    except Exception:
        def try_token():
            return uuid.uuid4().hex
    start_kernel(TEST_RUNS / try_token() / "workspace")

    match value:
        case "first":
            def match_token():
                return uuid.uuid4().hex
        case _:
            def match_token():
                return uuid.uuid4().hex
    start_kernel(TEST_RUNS / match_token() / "workspace")

    for item in items:
        def loop_token():
            return uuid.uuid4().hex
    else:
        def loop_token():
            return uuid.uuid4().hex
    start_kernel(TEST_RUNS / loop_token() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 4)

    def test_workflow_workspace_contract_allows_compact_match_helpers(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def test_paths(value):
    match value:
        case "first":
            def token():
                return uuid.uuid4().hex[:8]
        case _:
            def token():
                return uuid.uuid4().hex[:8]
    start_kernel(TEST_RUNS / token() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(violations, [])

    def test_workflow_workspace_contract_merges_helper_and_branch_facts(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def test_paths(condition):
    full_path = lambda token: TEST_RUNS / token

    def conditional_token():
        if condition:
            return uuid.uuid4().hex
        return uuid.uuid4().hex[:8]

    token = uuid.uuid4().hex
    if condition:
        token = uuid.uuid4().hex[:8]

    start_kernel(full_path(uuid.uuid4().hex) / "workspace")
    start_kernel(TEST_RUNS / conditional_token() / "workspace")
    start_kernel(TEST_RUNS / token / "workspace")
    start_kernel(TEST_RUNS / f"{uuid.uuid4().hex}/workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 4)

    def test_workflow_workspace_contract_invalidates_rebound_helpers(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

def test_paths():
    def token():
        return uuid.uuid4().hex
    token = application_token_provider
    start_kernel(TEST_RUNS / token() / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(violations, [])

    def test_workflow_workspace_contract_fails_closed_across_control_flow(
        self,
    ) -> None:
        source = """
import uuid
from tests.video_workflow._test_run import module_test_root

TEST_RUNS = module_test_root(PROJECT_ROOT)

class Harness:
    def root(self, token=uuid.uuid4().hex):
        return TEST_RUNS / token

    @staticmethod
    def static_root(token):
        return TEST_RUNS / token

    def test_default(self):
        start_kernel(self.root() / "workspace")
        start_kernel(self.static_root(uuid.uuid4().hex) / "workspace")

def test_control_flow(value, items, condition):
    token = uuid.uuid4().hex
    match value:
        case "compact":
            token = uuid.uuid4().hex[:8]
    start_kernel(TEST_RUNS / token / "workspace")

    loop_token = uuid.uuid4().hex
    for item in items:
        break
    else:
        loop_token = uuid.uuid4().hex[:8]
    start_kernel(TEST_RUNS / loop_token / "workspace")

    if condition:
        provider = lambda: uuid.uuid4().hex[:8]
    else:
        provider = lambda: uuid.uuid4().hex
    start_kernel(TEST_RUNS / provider() / "workspace")

def test_conditional_import(condition):
    if condition:
        import uuid
    else:
        uuid = application_uuid_provider
    start_kernel(TEST_RUNS / uuid.uuid4().hex / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(len(violations), 6)

    def test_workflow_workspace_contract_allows_trusted_helpers_and_non_uuids(
        self,
    ) -> None:
        source = """
from tests.video_workflow._test_run import (
    module_test_root,
    new_case_dir,
    new_workflow_workspace,
)

TEST_RUNS = module_test_root(PROJECT_ROOT)

def test_paths(value):
    start_kernel(new_case_dir("test.id", label="case") / "workspace")
    start_kernel(new_workflow_workspace("test.id", label="workflow"))
    start_kernel(TEST_RUNS / value.hex / "workspace")
    start_kernel(TEST_RUNS / "deadbeef" / "workspace")
"""
        violations = workflow_workspace_bypasses(
            source,
            Path("tests/video_workflow/test_external.py"),
        )

        self.assertEqual(violations, [])

    def test_registered_workflow_workspaces_use_compact_helper(self) -> None:
        violations: list[str] = []
        for source_path, suite_id in registered_contract_sources().items():
            if suite_id != "video-workflow":
                continue
            source = PROJECT_ROOT / PurePosixPath(source_path)
            violations.extend(
                workflow_workspace_bypasses(
                    source.read_text(encoding="utf-8"),
                    PurePosixPath(source_path),
                )
            )

        self.assertEqual(violations, [])

    def test_analysis_preserves_trusted_paths_through_string_conversions(
        self,
    ) -> None:
        source = """
from contextlib import contextmanager
import os
from pathlib import Path
from tests.project_test_runner._fixture_root import new_fixture_dir

@contextmanager
def external_string():
    yield str(new_fixture_dir("string-path"))

def test_external_boundaries():
    with external_string() as external:
        Path(external).write_text("{}")
    Path(os.fspath(new_fixture_dir("fspath-path"))).mkdir()
"""
        violations, _ = analyze_source(
            source,
            Path("tests/project_test_runner/test_external.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_analysis_roots_relative_string_and_path_sinks_in_project(
        self,
    ) -> None:
        source = """
import sqlite3
import subprocess
from pathlib import Path

def test_escape():
    Path("scratch/out").write_text("{}")
    open("待删除/x", "w")
    open(str("scratch/converted.txt"), "w")
    sqlite3.connect("scratch/state.sqlite3")
    subprocess.run(["tool"], cwd="scratch/subprocess")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 5)

    def test_analysis_keeps_absolute_strings_external_and_text_non_path(
        self,
    ) -> None:
        source = """
def test_external_and_text_values():
    open(r"D:\\tests\\external.txt", "w")
    open("/tmp/external.txt", "w")
    "ordinary text".replace("ordinary")
    "https://example.test/artifact".replace("https")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_analysis_propagates_helper_sequence_assignments(self) -> None:
        source = """
import sqlite3
from tests.video_workflow._test_run import new_case_dir

def pair():
    return object(), new_case_dir("test.id", label="pair")

def database_path():
    runtime, root = pair()
    return root / "state.sqlite3"

def journal_path():
    [runtime, root] = pair()
    return root / "journal.json"

def test_external_boundaries():
    database = database_path()
    sqlite3.connect(database)
    journal_path().write_text("{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_analysis_qualifies_explicit_local_trusted_provider_by_source(
        self,
    ) -> None:
        source = """
def build_decision_ready_authority():
    return choose_at_runtime()

def test_external_boundaries():
    build_decision_ready_authority().write_text("{}")
"""
        trusted_violations, _ = analyze_source(
            source,
            Path(
                "tests/video_workflow/"
                "test_source_publication_integration.py"
            ),
            {},
        )
        untrusted_violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_other.py"),
            {},
        )
        self.assertEqual(trusted_violations, [])
        self.assertEqual(len(untrusted_violations), 1)

    def test_analysis_trusts_only_fully_qualified_workflow_workspace_provider(
        self,
    ) -> None:
        trusted_source = """
from tests.video_workflow._test_run import new_workflow_workspace

def test_external_boundaries():
    new_workflow_workspace("test.id", label="trusted").write_text("{}")
"""
        untrusted_source = """
from tests.video_workflow.test_helpers import new_workflow_workspace

def test_external_boundaries():
    new_workflow_workspace("test.id", label="untrusted").write_text("{}")
"""
        trusted_violations, _ = analyze_source(
            trusted_source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        untrusted_violations, _ = analyze_source(
            untrusted_source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        self.assertEqual(trusted_violations, [])
        self.assertEqual(len(untrusted_violations), 1)

    def test_analysis_prefers_imported_names_over_unrelated_local_helpers(
        self,
    ) -> None:
        source = """
from unittest.mock import patch
from tests.video_workflow._test_run import new_case_dir

class Harness:
    def patch(self):
        destination = choose_at_runtime()
        destination.write_text("{}")

class Tests:
    def test_external_boundaries(self):
        with patch("os.name", "nt"):
            new_case_dir("test.id", label="patched").mkdir()
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_analysis_merges_proven_receiver_attribute_side_effects(
        self,
    ) -> None:
        source = """
import sqlite3
from production.runtime import Runtime
from tests.video_workflow._test_run import new_case_dir

class Harness:
    def initialize(self):
        self.workspace = new_case_dir("test.id", label="initialized")
        local_only = choose_at_runtime()

    def test_external_boundaries(self):
        self.initialize()
        migrated = Runtime(self.workspace)
        sqlite3.connect(migrated.control_store.path)
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_analysis_does_not_treat_bytes_replace_as_a_path_sink(self) -> None:
        source = """
def test_bytes_transform(source):
    payload = source.read_bytes()
    normalized = payload.replace(b"\\n", b"\\r\\n")
    return normalized
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_external.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_analysis_covers_extended_filesystem_and_library_sinks(self) -> None:
        source = """
import fitz
import os
import sqlite3
import tempfile
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_escape(mode):
    target = PROJECT_ROOT / "scratch" / "result"
    os.replace("source", target)
    os.link("source", target)
    os.symlink("source", target)
    os.utime(target)
    os.chmod(target, 0o600)
    target.hardlink_to("source")
    target.symlink_to("source")
    sqlite3.connect(target)
    fitz.open().save(target)
    tempfile.NamedTemporaryFile()
    open(target, mode)
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 11)

    def test_analysis_checks_shutil_destination_and_subprocess_outputs(self) -> None:
        source = """
import shutil
import subprocess
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_escape():
    shutil.copy(PROJECT_ROOT / "committed.txt", "external-copy.txt")
    shutil.copy("external-source.txt", PROJECT_ROOT / "scratch" / "copy.txt")
    subprocess.run(
        ["tool", "--output", PROJECT_ROOT / "scratch" / "result.json"],
        cwd=PROJECT_ROOT / "scratch",
    )
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 4)

    def test_analysis_collects_helpers_before_visiting_callers(self) -> None:
        source = """
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def test_escape():
    destination = repo_destination()
    destination.write_text("{}")

def repo_destination():
    return PROJECT_ROOT / "scratch" / "result.json"
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_propagates_repo_paths_through_indirect_helpers(self) -> None:
        source = """
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def destination():
    return first()

def first():
    return second()

def second():
    return PROJECT_ROOT / "scratch" / "result.json"

def test_escape():
    destination().write_text("{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_propagates_writes_through_indirect_helper_calls(self) -> None:
        source = """
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def first(destination):
    second(destination)

def second(destination):
    destination.mkdir()

def test_escape():
    first(PROJECT_ROOT / "scratch")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_rejects_aliased_os_mutators_and_path_constructor(self) -> None:
        source = """
from os import mkdir as make_directory, remove as erase
from pathlib import Path as ProjectPath
PROJECT_ROOT = ProjectPath(__file__).resolve().parents[2]

def test_escape():
    directory = PROJECT_ROOT / "scratch"
    make_directory(directory)
    erase(directory / "result.json")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 2)

    def test_analysis_fails_closed_for_unknown_paths_at_known_write_sinks(self) -> None:
        source = """
def unresolved():
    return choose_at_runtime()

def test_escape():
    unresolved().write_text("{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_fails_closed_for_direct_unknown_assignment(self) -> None:
        source = """
def test_escape():
    destination = choose_at_runtime()
    destination.write_text("{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_propagates_unknown_arguments_and_attribute_assignments(
        self,
    ) -> None:
        source = """
def write_result(destination):
    destination.write_text("{}")

def test_escape(holder):
    write_result(choose_at_runtime())
    holder.destination = choose_at_runtime()
    holder.destination.mkdir()
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 2)

    def test_analysis_fails_closed_for_branch_dependent_helper_results(self) -> None:
        source = """
def destination(enabled):
    if enabled:
        return choose_at_runtime()
    return other_runtime_choice()

def test_escape():
    destination(True).write_bytes(b"{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_rejects_untrusted_imported_write_receivers(self) -> None:
        source = """
from production.storage import artifact_store, make_artifact_store
import production.runtime as runtime

def test_production_objects_are_not_repo_paths():
    artifact_store.write_text("{}")
    make_artifact_store().mkdir()
    runtime.destination().write_bytes(b"{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_production.py"),
            {},
        )
        self.assertEqual(len(violations), 3)

    def test_analysis_allows_opaque_fixture_and_container_lineage(self) -> None:
        source = """
class ProductionFixtureTests:
    def test_production_objects_are_not_repo_paths(self):
        kernel, run_dir = self.ready_runtime()
        kernel.control_store.path.open("r+b")
        paths = list(run_dir.iterdir())
        paths[0].write_bytes(b"{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_production.py"),
            {},
        )
        self.assertEqual(violations, [])

    def test_analysis_fails_closed_for_unknown_test_parameters(self) -> None:
        source = """
def test_escape(destination):
    destination.write_text("{}")
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

    def test_analysis_fails_closed_for_recursive_helper_results(self) -> None:
        source = """
def recursive_destination():
    return recursive_destination()

def test_escape():
    recursive_destination().mkdir()
"""
        violations, _ = analyze_source(
            source,
            Path("tests/video_workflow/test_escape.py"),
            {},
        )
        self.assertEqual(len(violations), 1)

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
        source_suites = registered_contract_sources()
        self.assertEqual(68, len(source_suites))
        self.assertEqual(5, len(set(source_suites.values())))
        allowed = load_local_write_exceptions(source_suites)
        self.assertEqual(2, len(allowed))

        violations: list[str] = []
        used_exceptions: set[tuple[str, str, str, str]] = set()
        for source_path, suite_id in sorted(source_suites.items()):
            source = PROJECT_ROOT / PurePosixPath(source_path)
            source_text = source.read_text(encoding="utf-8")
            source_violations, source_used = analyze_source(
                source_text,
                PurePosixPath(source_path),
                allowed,
                suite_id=suite_id,
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
