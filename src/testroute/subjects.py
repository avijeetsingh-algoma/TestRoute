"""Loads and validates a function under test.

Component: C2 (Subject Loader and Input Validator).

Responsibility:
    Turns a Python file plus an entry-point name into a :class:`Subject`,
    after checking (spec C2):
    * the module name derived from the file name is a valid identifier and
      cannot be mistaken for a test file or shadow a module tests need;
    * the source parses;
    * it defines the entry point as a top-level function;
    * it imports in under 1 s in a sandboxed subprocess (D2: "imports without
      side effects"), and the imported entry point is callable.
    The same checks guard ``run.bat`` input (FB9) and the HumanEval subjects
    (spec Section 9.1).

Receives: a source file path (or source text), an entry-point name, and an
    optional function id (HumanEval id for experiment subjects).
Produces: :class:`Subject`.
Talks to: ``sandbox`` (import check). Used by C1 and the controller (C10);
    its output feeds C3 (prompts), C8 (tests) and C9 (mutants).
Spec sections: 5 (C2), 9.1 (screening uses the same import limit).
Feedback IDs: FB9 (any Python file plus function name is accepted, with
    readable errors).
"""

from __future__ import annotations

import ast
import json
import keyword
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from testroute.sandbox import Workspace, build_sandbox_env, run_process
from testroute.static_check import ALLOWED_STDLIB, DENYLISTED_MODULES

# Spec C2: the module must import in under one second.
IMPORT_LIMIT_S = 1.0
# Wall-clock limit for the whole import-check process, which also includes
# interpreter start-up (slow on Windows when antivirus scans python.exe).
IMPORT_PROCESS_TIMEOUT_S = 20.0

# Child script for the import check: measure only the import itself, then
# report whether the entry point is callable, as one JSON line.
_IMPORT_PROBE = """
import importlib, json, sys, time
sys.path.insert(0, '.')
start = time.perf_counter()
try:
    module = importlib.import_module({module!r})
    elapsed = time.perf_counter() - start
    entry = getattr(module, {entry!r}, None)
    print(json.dumps({{"ok": True, "seconds": elapsed, "callable": callable(entry)}}))
except BaseException as exc:
    print(json.dumps({{"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:300]}}))
"""


class SubjectError(Exception):
    """Raised when a source file cannot be used as a function under test."""


@dataclass(frozen=True)
class Subject:
    """A validated function under test.

    Attributes:
        function_id: Identifier used in run ids and file names, for example
            ``HumanEval_12`` or the module name for ad hoc input.
        module_name: Import name of the module (file stem).
        entry_point: Name of the function under test.
        source: Full module source, with ``\\n`` line endings.
        source_path: Where the source came from (relative to the project root
            when possible), for records only.
        import_seconds: Measured import time.
    """

    function_id: str
    module_name: str
    entry_point: str
    source: str
    source_path: str
    import_seconds: float

    def to_dict(self) -> dict:
        """JSON-ready form, written to the trace as ``01_C2_subject.json``."""
        data = asdict(self)
        data["lines_of_code"] = len(self.source.splitlines())
        return data


def validate_module_name(module_name: str) -> None:
    """Check that a file stem can safely be imported by generated tests.

    Raises:
        SubjectError: when the name is not an identifier, is a keyword,
            starts with ``test`` (pytest would collect it as a test file), or
            equals a module the tests import (pytest, allowlisted or
            denylisted stdlib modules), which the subject would shadow.
    """
    if not module_name.isidentifier() or keyword.iskeyword(module_name):
        raise SubjectError(f"'{module_name}' is not a valid Python module name; rename the file")
    if module_name.startswith("test"):
        raise SubjectError(f"module name '{module_name}' starts with 'test', which pytest would treat as a test file")
    if module_name in ALLOWED_STDLIB | DENYLISTED_MODULES | {"pytest", "conftest"}:
        raise SubjectError(f"module name '{module_name}' would shadow a standard module; rename the file")


def load_subject_source(
    source: str,
    module_name: str,
    entry_point: str,
    *,
    function_id: str | None = None,
    source_path: str = "",
    check_import: bool = True,
) -> Subject:
    """Validate source text as a function under test.

    Args:
        source: Module source.
        module_name: Import name to give the module.
        entry_point: Function under test.
        function_id: Id for records; defaults to ``module_name``.
        source_path: Origin of the source, for records.
        check_import: Run the sandboxed import check. Only unit tests that
            exercise parsing alone turn this off.

    Returns:
        A :class:`Subject`.

    Raises:
        SubjectError: on any failed check, with a message saying how to fix it.

    Side effects:
        Starts one sandboxed Python subprocess when ``check_import`` is True.
    """
    validate_module_name(module_name)
    # Normalize line endings so coverage line numbers, prompts and mutants all
    # refer to the same lines regardless of how the file was checked out.
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    if not source.endswith("\n"):
        source += "\n"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SubjectError(f"{module_name}.py does not parse: {exc.msg} (line {exc.lineno})") from exc

    top_functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if entry_point not in top_functions:
        listed = ", ".join(sorted(top_functions)) or "none"
        raise SubjectError(f"{module_name}.py has no top-level function '{entry_point}' (functions found: {listed})")

    seconds = _check_import(source, module_name, entry_point) if check_import else 0.0
    return Subject(
        function_id=function_id or module_name,
        module_name=module_name,
        entry_point=entry_point,
        source=source,
        source_path=source_path,
        import_seconds=round(seconds, 4),
    )


def load_subject(path: Path, entry_point: str | None = None, *, function_id: str | None = None) -> Subject:
    """Load a subject from a file.

    Args:
        path: The Python file containing the function.
        entry_point: Function under test. When None, the file must contain
            exactly one public top-level function, which is used.
        function_id: Id for records; defaults to the module name.

    Returns:
        A validated :class:`Subject`.

    Raises:
        SubjectError: when the file is missing or any check fails.

    Side effects:
        Reads the file; starts one sandboxed subprocess for the import check.
    """
    if not path.is_file():
        raise SubjectError(f"file not found: {path}")
    source = path.read_text(encoding="utf-8")
    if not entry_point:
        entry_point = default_entry_point(source, path.stem)
    return load_subject_source(
        source, path.stem, entry_point, function_id=function_id, source_path=_display_path(path)
    )


def default_entry_point(source: str, module_name: str) -> str:
    """Pick the only public top-level function of a module.

    Raises:
        SubjectError: when the source does not parse or has zero or several
            public functions, so the user must name one explicitly.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SubjectError(f"{module_name}.py does not parse: {exc.msg} (line {exc.lineno})") from exc
    public = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
    if len(public) != 1:
        raise SubjectError(
            f"{module_name}.py has {len(public)} public functions ({', '.join(public) or 'none'}); "
            "pass the function name explicitly"
        )
    return public[0]


def _display_path(path: Path) -> str:
    """Path relative to the project root when possible, else absolute."""
    from testroute import paths

    resolved = path.resolve()
    try:
        return resolved.relative_to(paths.PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _check_import(source: str, module_name: str, entry_point: str) -> float:
    """Import the module in a sandboxed subprocess and time the import.

    Returns:
        Import time in seconds.

    Raises:
        SubjectError: if the import fails, takes 1 s or longer, hangs, or the
            entry point is not callable after import.
    """
    with Workspace(prefix="testroute_import_") as ws:
        ws.write(f"{module_name}.py", source)
        probe = _IMPORT_PROBE.format(module=module_name, entry=entry_point)
        proc = run_process([sys.executable, "-c", probe], ws.path, build_sandbox_env(ws.path), IMPORT_PROCESS_TIMEOUT_S)
    if proc.timed_out:
        raise SubjectError(f"importing {module_name} did not finish within {IMPORT_PROCESS_TIMEOUT_S:g} s")
    try:
        report = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise SubjectError(f"import check of {module_name} gave no result: {proc.stderr.strip()[-300:]}") from exc
    if not report.get("ok"):
        raise SubjectError(f"importing {module_name} failed: {report.get('error')}")
    if report["seconds"] >= IMPORT_LIMIT_S:
        raise SubjectError(f"importing {module_name} took {report['seconds']:.2f} s (limit {IMPORT_LIMIT_S:g} s)")
    if not report.get("callable"):
        raise SubjectError(f"{entry_point} is not callable after importing {module_name}")
    return float(report["seconds"])
