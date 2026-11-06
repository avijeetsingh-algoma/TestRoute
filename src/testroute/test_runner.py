"""Runs a generated pytest file against a module, with coverage.

Component: C8 (Sandbox Test Runner), test runner half. ``sandbox.py`` holds
the workspace, environment and process tree handling.

Responsibility:
    1. :func:`run_suite` copies the module and the test file into a fresh
       workspace and runs ``python -m coverage run --branch -m pytest`` there,
       with JUnit XML output, pytest-timeout at 5 s per test and a 60 s limit
       for the whole suite (spec C8). It returns the outcome of every test
       case, plus line and branch coverage of the module.
    2. :func:`filter_failing_tests` removes every test function that did not
       pass, by deleting its source lines found through ``ast``, to build the
       filtered suite. A parametrized function with any failing case is
       removed whole (spec C8).

    Windows detail that shapes the design: pytest-timeout cannot use signals
    on Windows and falls back to its "thread" method, which dumps stacks and
    ends the whole pytest process when a test hangs. No JUnit file is written
    in that case. The runner therefore uses verbose output (each test id is
    printed before it starts) to find the hung test, records it as a TIMEOUT,
    deselects it, and runs the remaining tests again, all inside the one
    suite-level time budget.

Receives: module name and source, test source, time limits.
Produces: :class:`SuiteRunResult` with :class:`TestCaseResult` items and an
    optional :class:`CoverageResult`; filtered test source.
Talks to: ``sandbox`` (workspace and processes). Called by the controller
    (C10); the mutation runner (C9) reuses :func:`filter_failing_tests` input.
Spec sections: 5 (C8), 6.6 (baseline time for mutant limits), 8 (V4, V5,
    V6; COLLECTION_ERROR, TEST_FAIL, TIMEOUT, TOOL_ERROR).
Feedback IDs: FB3 (timeouts are their own outcome).
"""

from __future__ import annotations

import ast
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from testroute.sandbox import ProcessResult, Workspace, build_sandbox_env, run_process

# Suite run status values (see SuiteRunResult.status).
STATUS_OK = "OK"
STATUS_COLLECTION_ERROR = "COLLECTION_ERROR"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_TOOL_ERROR = "TOOL_ERROR"

# Per-case outcome values (see TestCaseResult.outcome).
OUTCOME_PASSED = "passed"
OUTCOME_FAILED = "failed"
OUTCOME_ERROR = "error"
OUTCOME_SKIPPED = "skipped"
OUTCOME_TIMEOUT = "timeout"

# Longest single error message kept per test. The prompt builder may shorten
# messages further to fit the context budget (spec C3).
MAX_MESSAGE_CHARS = 300

# Upper bound on hung-test recovery rounds, so a file with many hanging tests
# cannot loop for long; the suite time budget normally ends it first.
MAX_TIMEOUT_ROUNDS = 10

# pytest-timeout prints this banner when it ends a hung test (thread method).
_TIMEOUT_BANNER = re.compile(r"^(?P<nodeid>\S+::\S+)\s+\+{3,} Timeout \+{3,}", re.MULTILINE)


@dataclass
class TestCaseResult:
    """Outcome of one collected test case.

    Attributes:
        nodeid: pytest node id, for example ``test_mod.py::test_x[1]``.
        class_name: Enclosing test class, or None for a module-level test.
        function: Test function name without parametrization suffix.
        outcome: passed, failed, error, skipped or timeout.
        error_type: Exception type name for a non-passing case, else "".
        message: First line of the failure message, at most
            MAX_MESSAGE_CHARS characters.
        duration_s: Time reported by JUnit, 0 for timeouts.
    """

    __test__ = False  # Stop pytest from collecting this class as a test.

    nodeid: str
    class_name: str | None
    function: str
    outcome: str
    error_type: str = ""
    message: str = ""
    duration_s: float = 0.0


@dataclass
class CoverageResult:
    """Line and branch coverage of the module under test (coverage.py JSON).

    Attributes:
        line_percent: Executed statements / statements x 100.
        branch_percent: Executed branch arcs / branch arcs x 100; 100 when the
            module has no branches.
        executed_lines: Line numbers executed at least once.
        missing_lines: Statement line numbers never executed.
        missing_branches: (from_line, to_line) arcs never taken. A negative
            to_line is coverage.py's code for "exit from the code object that
            starts at that line".
        num_statements: Statement count.
        num_branches: Branch arc count.
    """

    line_percent: float
    branch_percent: float
    executed_lines: list[int]
    missing_lines: list[int]
    missing_branches: list[tuple[int, int]]
    num_statements: int
    num_branches: int


@dataclass
class SuiteRunResult:
    """Result of running one test file.

    Attributes:
        status: OK (pytest ran to completion, tests may still fail),
            COLLECTION_ERROR (V4 failed), TIMEOUT (whole-suite limit hit), or
            TOOL_ERROR (pytest or coverage crashed for a reason that is not the
            test file's fault).
        tests: Per-case results; for TIMEOUT it holds cases finished so far.
        coverage: Coverage of the module, when the run completed.
        duration_s: Total wall-clock time over all rounds.
        message: Explanation for a non-OK status (first lines of output).
        rounds: Number of pytest processes started (1 unless a test hung).
    """

    status: str
    tests: list[TestCaseResult] = field(default_factory=list)
    coverage: CoverageResult | None = None
    duration_s: float = 0.0
    message: str = ""
    rounds: int = 0

    @property
    def passed(self) -> list[TestCaseResult]:
        """Cases that passed."""
        return [t for t in self.tests if t.outcome == OUTCOME_PASSED]

    @property
    def not_passed(self) -> list[TestCaseResult]:
        """Cases that failed, errored, were skipped or timed out."""
        return [t for t in self.tests if t.outcome != OUTCOME_PASSED]

    def to_dict(self) -> dict:
        """JSON-ready form for records and trace files."""
        return asdict(self)


def test_file_name(module_name: str) -> str:
    """File name used for a generated test file of ``module_name``."""
    return f"test_{module_name}.py"


def run_suite(
    module_name: str,
    module_source: str,
    test_source: str,
    *,
    per_test_timeout_s: float = 5,
    suite_timeout_s: float = 60,
    with_coverage: bool = True,
) -> SuiteRunResult:
    """Run a pytest file against a module in a fresh sandbox workspace.

    Args:
        module_name: Import name of the module under test (file stem).
        module_source: Source of the module (reference or mutant).
        test_source: Source of the generated test file.
        per_test_timeout_s: pytest-timeout limit for each test.
        suite_timeout_s: Wall-clock budget for the whole file, shared by all
            recovery rounds.
        with_coverage: Measure line and branch coverage of the module.

    Returns:
        A :class:`SuiteRunResult`.

    Raises:
        OSError: if Python cannot be started at all.

    Side effects:
        Creates and deletes a temp folder; starts pytest (and coverage)
        subprocesses with a stripped environment.
    """
    test_name = test_file_name(module_name)
    start = time.perf_counter()
    timed_out_cases: list[TestCaseResult] = []
    rounds = 0

    with Workspace() as ws:
        ws.write(f"{module_name}.py", module_source)
        ws.write(test_name, test_source)
        # An empty pytest.ini fixes the rootdir to the workspace so pytest never
        # picks up a configuration file from a parent folder of the temp dir.
        ws.write("pytest.ini", "[pytest]\n")
        env = build_sandbox_env(
            ws.path,
            {
                "COVERAGE_FILE": str(ws.path / ".coverage"),
                # Only the plugins we name on the command line are loaded, so
                # whatever else is installed cannot change test behavior.
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            },
        )

        while True:
            rounds += 1
            remaining = suite_timeout_s - (time.perf_counter() - start)
            if remaining <= 0 or rounds > MAX_TIMEOUT_ROUNDS:
                return SuiteRunResult(
                    STATUS_TIMEOUT,
                    tests=timed_out_cases,
                    duration_s=time.perf_counter() - start,
                    message=f"test suite exceeded {suite_timeout_s:g} s",
                    rounds=rounds - 1,
                )
            junit = ws.path / "junit.xml"
            junit.unlink(missing_ok=True)
            cmd = _pytest_command(module_name, test_name, per_test_timeout_s, with_coverage)
            for case in timed_out_cases:
                cmd += ["--deselect", case.nodeid]
            proc = run_process(cmd, ws.path, env, remaining)

            if proc.timed_out:
                return SuiteRunResult(
                    STATUS_TIMEOUT,
                    tests=timed_out_cases,
                    duration_s=time.perf_counter() - start,
                    message=f"test suite exceeded {suite_timeout_s:g} s",
                    rounds=rounds,
                )

            if not junit.is_file():
                # No JUnit file: either pytest-timeout ended a hung test (see
                # module docstring) or something crashed before pytest wrote it.
                hung = _find_hung_test(proc.stdout)
                if hung is not None and hung not in {c.nodeid for c in timed_out_cases}:
                    timed_out_cases.append(_timeout_case(hung, per_test_timeout_s))
                    continue
                return SuiteRunResult(
                    STATUS_TOOL_ERROR,
                    tests=timed_out_cases,
                    duration_s=time.perf_counter() - start,
                    message="pytest ended without a JUnit report: " + _tail(proc),
                    rounds=rounds,
                )

            cases, collection_error = _parse_junit(junit)
            status, message = _classify_exit(proc, collection_error, bool(cases))
            coverage = None
            if status == STATUS_OK and with_coverage:
                coverage = _read_coverage(ws.path, module_name, env)
                if coverage is None:
                    status, message = STATUS_TOOL_ERROR, "coverage.py produced no report for the module"
            return SuiteRunResult(
                status,
                tests=cases + timed_out_cases,
                coverage=coverage,
                duration_s=time.perf_counter() - start,
                message=message,
                rounds=rounds,
            )


def _pytest_command(module_name: str, test_name: str, per_test_timeout_s: float, with_coverage: bool) -> list[str]:
    """Build the pytest command line (optionally wrapped in coverage run)."""
    pytest_args = [
        "-m",
        "pytest",
        test_name,
        # Verbose: prints each node id before the test runs, needed to find a
        # test that pytest-timeout ended (see module docstring).
        "-v",
        "--no-header",
        "--tb=short",
        "-p",
        "no:cacheprovider",
        "-p",
        "pytest_timeout",
        f"--timeout={per_test_timeout_s:g}",
        # Explicit: signals are not available on Windows.
        "--timeout-method=thread",
        "--junitxml=junit.xml",
    ]
    if not with_coverage:
        return [sys.executable, *pytest_args]
    return [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "--branch",
        # Measure only the module under test, never the test file or pytest.
        f"--include={module_name}.py",
        *pytest_args,
    ]


def _find_hung_test(stdout: str) -> str | None:
    """Return the node id that pytest-timeout ended, if its banner is present."""
    match = _TIMEOUT_BANNER.search(stdout)
    return match.group("nodeid") if match else None


def _split_nodeid(nodeid: str) -> tuple[str | None, str]:
    """Split ``file::Class::func[param]`` into (class or None, func)."""
    parts = nodeid.split("::")[1:]
    func = parts[-1].split("[", 1)[0] if parts else nodeid
    class_name = parts[0] if len(parts) > 1 else None
    return class_name, func


def _timeout_case(nodeid: str, per_test_timeout_s: float) -> TestCaseResult:
    """Build the result entry for a test ended by pytest-timeout."""
    class_name, func = _split_nodeid(nodeid)
    return TestCaseResult(
        nodeid=nodeid,
        class_name=class_name,
        function=func,
        outcome=OUTCOME_TIMEOUT,
        error_type="Timeout",
        message=f"test did not finish within {per_test_timeout_s:g} s",
    )


def _error_type_from_message(message: str) -> str:
    """Infer the exception type name shown in the EXEC feedback line.

    pytest writes plain assertion failures as ``assert 0 == 1`` and other
    exceptions as ``ValueError: ...``; ``pytest.raises`` that did not fire
    reads ``Failed: DID NOT RAISE ...``.
    """
    first = message.strip().splitlines()[0] if message.strip() else ""
    match = re.match(r"^([A-Za-z_][\w.]*)(?::|$)", first)
    if first.startswith("assert"):
        return "AssertionError"
    if match:
        return match.group(1).rsplit(".", 1)[-1]
    return "Error"


def _clean_message(message: str) -> str:
    """First line of a message, with a leading ``Type:`` prefix removed."""
    first = message.strip().splitlines()[0] if message.strip() else ""
    first = re.sub(r"^[A-Za-z_][\w.]*:\s*", "", first) if not first.startswith("assert") else first
    return first[:MAX_MESSAGE_CHARS]


def _parse_junit(junit: Path) -> tuple[list[TestCaseResult], str | None]:
    """Parse pytest's JUnit XML.

    Returns:
        (cases, collection_error). A collection error appears in JUnit as a
        testcase without a test function (empty classname) carrying an
        ``<error>``; its message is returned instead of a case.
    """
    root = ET.parse(junit).getroot()
    cases: list[TestCaseResult] = []
    collection_error: str | None = None
    for tc in root.iter("testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        problem = next((child for child in tc if child.tag in ("failure", "error", "skipped")), None)
        if not classname:
            # pytest reports a file that failed to import this way.
            if problem is not None:
                collection_error = (problem.text or problem.get("message", "")).strip()
            continue
        # classname is "test_mod" or "test_mod.TestClass".
        module_part, _, class_part = classname.partition(".")
        nodeid = f"{module_part}.py::" + (f"{class_part}::" if class_part else "") + name
        outcome, error_type, message = OUTCOME_PASSED, "", ""
        if problem is not None:
            raw = problem.get("message", "") or (problem.text or "")
            if problem.tag == "skipped":
                outcome, error_type, message = OUTCOME_SKIPPED, "Skipped", _clean_message(raw)
            else:
                outcome = OUTCOME_FAILED if problem.tag == "failure" else OUTCOME_ERROR
                error_type, message = _error_type_from_message(raw), _clean_message(raw)
        cases.append(
            TestCaseResult(
                nodeid=nodeid,
                class_name=class_part or None,
                function=name.split("[", 1)[0],
                outcome=outcome,
                error_type=error_type,
                message=message,
                duration_s=float(tc.get("time", 0) or 0),
            )
        )
    return cases, collection_error


def _classify_exit(proc: ProcessResult, collection_error: str | None, has_cases: bool) -> tuple[str, str]:
    """Map the pytest exit code to a suite status.

    pytest exit codes: 0 all passed, 1 some failed, 2 interrupted (also used
    for collection errors), 3 internal error, 4 usage error, 5 no tests
    collected. Codes 2 and 5 are the test file's fault (COLLECTION_ERROR,
    validation check V4); 3 and 4 point at our harness (TOOL_ERROR).
    """
    code = proc.exit_code
    if collection_error is not None or code == 2:
        return STATUS_COLLECTION_ERROR, (collection_error or _tail(proc))[: MAX_MESSAGE_CHARS * 3]
    if code == 5 or (code in (0, 1) and not has_cases):
        return STATUS_COLLECTION_ERROR, "pytest collected no tests"
    if code in (0, 1):
        return STATUS_OK, ""
    return STATUS_TOOL_ERROR, f"pytest exit code {code}: " + _tail(proc)


def _read_coverage(workdir: Path, module_name: str, env: dict[str, str]) -> CoverageResult | None:
    """Export coverage data to JSON and read the entry of the module file."""
    proc = run_process(
        [sys.executable, "-m", "coverage", "json", "-q", "-o", "coverage.json"], workdir, env, 30
    )
    report = workdir / "coverage.json"
    if proc.exit_code != 0 or not report.is_file():
        return None
    files = json.loads(report.read_text(encoding="utf-8")).get("files", {})
    entry = files.get(f"{module_name}.py")
    if entry is None:
        return None
    summary = entry["summary"]
    statements = int(summary["num_statements"])
    branches = int(summary.get("num_branches", 0))
    line_pct = 100.0 * summary["covered_lines"] / statements if statements else 100.0
    branch_pct = 100.0 * summary.get("covered_branches", 0) / branches if branches else 100.0
    return CoverageResult(
        line_percent=round(line_pct, 2),
        branch_percent=round(branch_pct, 2),
        executed_lines=sorted(entry.get("executed_lines", [])),
        missing_lines=sorted(entry.get("missing_lines", [])),
        missing_branches=[(int(a), int(b)) for a, b in entry.get("missing_branches", [])],
        num_statements=statements,
        num_branches=branches,
    )


def _tail(proc: ProcessResult, lines: int = 15) -> str:
    """Last lines of combined output, used in error messages."""
    text = (proc.stdout + "\n" + proc.stderr).strip().splitlines()
    return "\n".join(text[-lines:])


def filter_failing_tests(test_source: str, results: list[TestCaseResult]) -> tuple[str, list[str]]:
    """Remove every test function that has a non-passing case.

    Lines are deleted rather than the tree being re-generated with
    ``ast.unparse``, so the filtered file keeps the model's comments and
    formatting and stays readable next to the raw file.

    Args:
        test_source: The raw test file.
        results: Per-case results of running it.

    Returns:
        (filtered_source, removed) where ``removed`` lists the removed tests
        as ``func`` or ``Class.func``.

    Raises:
        SyntaxError: if ``test_source`` does not parse (callers only pass files
            that passed C7).

    Side effects:
        None.
    """
    failing = {(r.class_name, r.function) for r in results if r.outcome != OUTCOME_PASSED}
    if not failing:
        return test_source, []
    tree = ast.parse(test_source)
    spans: list[tuple[int, int, str]] = []

    def _span(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> tuple[int, int]:
        # Decorators (for example @pytest.mark.parametrize) belong to the test
        # and must be removed with it.
        first = min([node.lineno] + [d.lineno for d in node.decorator_list])
        return first, node.end_lineno or node.lineno

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (None, node.name) in failing:
            spans.append((*_span(node), node.name))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (node.name, item.name) in failing:
                    spans.append((*_span(item), f"{node.name}.{item.name}"))

    lines = test_source.splitlines(keepends=True)
    drop: set[int] = set()
    for first, last, _name in spans:
        drop.update(range(first, last + 1))
    kept = [line for number, line in enumerate(lines, start=1) if number not in drop]
    filtered = "".join(kept)
    # A class whose only method was removed would be left with an empty body,
    # which is a syntax error; a "pass" keeps the file valid.
    filtered = _repair_empty_classes(filtered)
    return filtered, [name for _first, _last, name in spans]


def _repair_empty_classes(source: str) -> str:
    """Insert ``pass`` into classes left without a body by line removal."""
    try:
        ast.parse(source)
        return source
    except SyntaxError:
        pass
    lines = source.splitlines(keepends=True)
    out: list[str] = []
    for index, line in enumerate(lines):
        out.append(line)
        stripped = line.strip()
        if stripped.startswith("class ") and stripped.endswith(":"):
            indent = line[: len(line) - len(line.lstrip())]
            following = next((l for l in lines[index + 1 :] if l.strip()), "")
            next_indent = following[: len(following) - len(following.lstrip())]
            if not following or len(next_indent) <= len(indent):
                out.append(f"{indent}    pass\n")
    return "".join(out)
