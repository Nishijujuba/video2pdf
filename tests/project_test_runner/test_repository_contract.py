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
