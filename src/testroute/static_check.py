"""Static validation of a generated test file before it is executed.

Component: C7 (Static Validator).

Responsibility:
    Implements validation checks V2 (syntax) and V3 (static rules) with the
    ``ast`` module, so unsafe or useless files are rejected without running.
    The checks, in the order of spec C7, each with its error type:

    1. SYNTAX_ERROR      the file does not parse.
    2. WRONG_IMPORT      the file does not import the entry point from the
                         module under test (``from <module> import <entry>``
                         or ``import <module>``).
    3. DISALLOWED_IMPORT an import outside pytest, the module under test and
                         the stdlib allowlist below.
    4. NO_TESTS          no ``test_`` function (module level, or a method of a
                         ``Test*`` class).
    5. NO_ASSERT         a test without an ``assert`` statement or a
                         ``pytest.raises`` call.
    6. UNSAFE            a forbidden name (``open``, ``eval``, ...) or an
                         import of a denylisted module (``os``, ``sys``, ...).

    Every check runs and all violations are reported; the first failing check
    in the order above is the recorded error type (spec Section 8). This is
    the first safety barrier; the subprocess sandbox (C8) is the second.

Receives: test file source, module name, entry point name.
Produces: :class:`StaticResult`.
Talks to: called by the controller (C10) after C6; passing files go to C8.
Spec sections: 5 (C7), 8 (V2, V3 and their error types).
Feedback IDs: none directly.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Error types in the order checks are applied (spec C7).
SYNTAX_ERROR = "SYNTAX_ERROR"
WRONG_IMPORT = "WRONG_IMPORT"
DISALLOWED_IMPORT = "DISALLOWED_IMPORT"
NO_TESTS = "NO_TESTS"
NO_ASSERT = "NO_ASSERT"
UNSAFE = "UNSAFE"
CHECK_ORDER: tuple[str, ...] = (SYNTAX_ERROR, WRONG_IMPORT, DISALLOWED_IMPORT, NO_TESTS, NO_ASSERT, UNSAFE)

# Names that may not appear anywhere in a test file (spec C7): they read or
# write files, run arbitrary code, wait for input, or end the process.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {"open", "eval", "exec", "compile", "__import__", "input", "breakpoint", "exit", "quit"}
)

# Attribute names used to escape a restricted namespace, for example
# ``().__class__.__base__.__subclasses__()``. Not in the spec list; added as
# hardening because without Docker the static check is the main barrier.
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {"__subclasses__", "__globals__", "__builtins__", "__code__", "__getattribute__", "__import__"}
)

# Modules whose import is UNSAFE (spec C7): process, file system, network,
# threads, native code, serialization, and sources of nondeterminism (random,
# time, datetime), which the T1 prompt also forbids.
DENYLISTED_MODULES: frozenset[str] = frozenset(
    {
        "os", "sys", "subprocess", "socket", "shutil", "pathlib", "io", "tempfile",
        "threading", "multiprocessing", "asyncio", "ctypes", "signal", "importlib",
        "urllib", "http", "pickle", "marshal", "glob", "random", "time", "datetime",
    }
)

# Standard library modules a test may import (spec C7 "allowlisted stdlib
# modules"). Chosen as pure computation helpers that tests of small
# HumanEval-style functions plausibly need; none touches the outside world.
ALLOWED_STDLIB: frozenset[str] = frozenset(
    {
        "math", "cmath", "decimal", "fractions", "numbers", "statistics",
        "itertools", "functools", "operator", "collections", "heapq", "bisect",
        "array", "copy", "string", "re", "textwrap", "unicodedata", "typing",
        "dataclasses", "enum", "abc", "json", "hashlib", "__future__",
    }
)


@dataclass
class StaticResult:
    """Outcome of V2 and V3.

    Attributes:
        ok: True when no check failed.
        error_type: First failing check in CHECK_ORDER, or None.
        message: Human-readable message for the first failure; also used in
            the EXEC feedback block as ``(whole file): <type>: <message>``.
        violations: Every violation found, as (error_type, message) pairs.
        test_names: Test functions found (``func`` or ``Class.func``).
    """

    ok: bool
    error_type: str | None
    message: str
    violations: list[tuple[str, str]] = field(default_factory=list)
    test_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSON-ready form for records and trace files."""
        return {
            "ok": self.ok,
            "error_type": self.error_type,
            "message": self.message,
            "violations": [{"error_type": t, "message": m} for t, m in self.violations],
            "test_names": self.test_names,
        }


def _top_module(name: str | None) -> str:
    """``collections.abc`` -> ``collections``."""
    return (name or "").split(".", 1)[0]


def _find_tests(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return (display name, node) for every test pytest would collect."""
    tests: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    tests.append((f"{node.name}.{item.name}", item))
    return tests


def _has_assertion(func: ast.AST, raises_aliases: set[str]) -> bool:
    """True when the function body contains ``assert`` or ``pytest.raises``.

    ``raises_aliases`` holds local names bound to ``pytest.raises`` through
    ``from pytest import raises``.
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr == "raises":
                if isinstance(target.value, ast.Name) and target.value.id == "pytest":
                    return True
            if isinstance(target, ast.Name) and target.id in raises_aliases:
                return True
    return False


def check_test_file(source: str, module_name: str, entry_point: str) -> StaticResult:
    """Run checks V2 and V3 on a generated test file.

    Args:
        source: The extracted test file (output of C6).
        module_name: Module the tests must import from.
        entry_point: Function under test.

    Returns:
        A :class:`StaticResult`.

    Raises:
        Nothing; a syntax error is reported as SYNTAX_ERROR.

    Side effects:
        None. The file is never executed here.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None)
        detail = getattr(exc, "msg", str(exc))
        message = f"{detail} (line {line})" if line else detail
        return StaticResult(False, SYNTAX_ERROR, message, [(SYNTAX_ERROR, message)])

    violations: list[tuple[str, str]] = []
    imports_entry = False
    raises_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top_module(alias.name)
                if alias.name == module_name:
                    imports_entry = True
                elif top in DENYLISTED_MODULES:
                    violations.append((UNSAFE, f"imports forbidden module '{alias.name}'"))
                elif top not in ALLOWED_STDLIB and top != "pytest":
                    violations.append((DISALLOWED_IMPORT, f"imports '{alias.name}', which is not allowed"))
        elif isinstance(node, ast.ImportFrom):
            top = _top_module(node.module)
            if node.level:
                violations.append((DISALLOWED_IMPORT, "uses a relative import"))
            elif node.module == module_name:
                if any(alias.name in (entry_point, "*") for alias in node.names):
                    imports_entry = True
            elif top in DENYLISTED_MODULES:
                violations.append((UNSAFE, f"imports from forbidden module '{node.module}'"))
            elif top == "pytest":
                raises_aliases.update(a.asname or a.name for a in node.names if a.name == "raises")
            elif top not in ALLOWED_STDLIB:
                violations.append((DISALLOWED_IMPORT, f"imports from '{node.module}', which is not allowed"))
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append((UNSAFE, f"uses forbidden name '{node.id}' (line {node.lineno})"))
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            violations.append((UNSAFE, f"uses forbidden attribute '{node.attr}' (line {node.lineno})"))

    if not imports_entry:
        violations.append((WRONG_IMPORT, f"does not import {entry_point} from {module_name}"))

    tests = _find_tests(tree)
    if not tests:
        violations.append((NO_TESTS, "defines no test_ function"))
    for name, func in tests:
        if not _has_assertion(func, raises_aliases):
            violations.append((NO_ASSERT, f"test {name} has no assert or pytest.raises"))

    test_names = [name for name, _ in tests]
    if not violations:
        return StaticResult(True, None, "", [], test_names)
    # The recorded error type is the first failing check in spec order, not
    # the first violation found while walking the tree.
    first_type = min((t for t, _ in violations), key=CHECK_ORDER.index)
    first_message = next(m for t, m in violations if t == first_type)
    return StaticResult(False, first_type, first_message, violations, test_names)
