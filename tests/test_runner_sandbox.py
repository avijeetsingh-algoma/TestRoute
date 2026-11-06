"""Unit tests for the sandbox and test runner (C8) and the subject loader (C2).

These start real pytest subprocesses on Windows, so they also check the
process handling: hung tests, whole-suite timeouts, collection errors and
the stripped environment (spec C8, Section 14).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from testroute.sandbox import Workspace, build_sandbox_env, run_process
from testroute.subjects import SubjectError, load_subject_source
from testroute.test_runner import filter_failing_tests, run_suite

MODULE = "def f(x):\n    if x > 0:\n        return 1\n    return 0\n"
HEADER = "import pytest\nfrom mod import f\n\n"
# A module that spends 1.5 s at import time, over the 1 s limit of spec C2.
SLOW_IMPORT = "import time\n_start = time.perf_counter()\nwhile time.perf_counter() - _start < 1.5:\n    pass\n"


def test_pass_fail_and_coverage() -> None:
    """Per-case outcomes, assertion messages and branch coverage come back from one run."""
    tests = HEADER + "def test_a():\n    assert f(1) == 1\n\ndef test_b():\n    assert f(0) == 1\n"
    result = run_suite("mod", MODULE, tests)
    assert result.status == "OK"
    outcomes = {t.function: t for t in result.tests}
    assert outcomes["test_a"].outcome == "passed"
    assert outcomes["test_b"].outcome == "failed" and outcomes["test_b"].error_type == "AssertionError"
    assert outcomes["test_b"].message.startswith("assert 0 == 1")
    assert result.coverage is not None and result.coverage.branch_percent == 100.0


def test_hung_test_is_timeout_and_others_still_run() -> None:
    """A hanging test becomes TIMEOUT; the remaining tests still get real outcomes (Windows thread method)."""
    tests = HEADER + "def test_ok():\n    assert f(1) == 1\n\ndef test_hang():\n    while True:\n        pass\n"
    result = run_suite("mod", MODULE, tests, per_test_timeout_s=1, suite_timeout_s=30)
    outcomes = {t.function: t.outcome for t in result.tests}
    assert result.status == "OK" and result.rounds == 2
    assert outcomes == {"test_ok": "passed", "test_hang": "timeout"}
    assert result.coverage is not None


def test_whole_suite_timeout() -> None:
    """A suite over its wall-clock budget is TIMEOUT and its process tree is killed."""
    tests = HEADER + "def test_slow():\n    while True:\n        pass\n"
    result = run_suite("mod", MODULE, tests, per_test_timeout_s=30, suite_timeout_s=2)
    assert result.status == "TIMEOUT"


def test_collection_error() -> None:
    """A test file that fails to import is a COLLECTION_ERROR (check V4)."""
    tests = "from mod import missing_name\n\ndef test_a():\n    assert True\n"
    result = run_suite("mod", MODULE, tests)
    assert result.status == "COLLECTION_ERROR" and "missing_name" in result.message


def test_filter_removes_parametrized_function_whole() -> None:
    """A parametrized test with one failing case is removed whole, decorator included (spec C8)."""
    tests = (
        HEADER
        + "@pytest.mark.parametrize('x', [1, 0])\ndef test_p(x):\n    assert f(x) == 1\n\n"
        + "def test_keep():\n    assert f(1) == 1\n"
    )
    result = run_suite("mod", MODULE, tests, with_coverage=False)
    filtered, removed = filter_failing_tests(tests, result.tests)
    assert removed == ["test_p"]
    assert "parametrize" not in filtered and "def test_keep" in filtered
    assert run_suite("mod", MODULE, filtered, with_coverage=False).not_passed == []


def test_filter_keeps_class_valid() -> None:
    """Removing the only method of a test class leaves a syntactically valid file."""
    tests = HEADER + "class TestC:\n    def test_x(self):\n        assert f(0) == 1\n\ndef test_keep():\n    assert f(1) == 1\n"
    result = run_suite("mod", MODULE, tests, with_coverage=False)
    filtered, removed = filter_failing_tests(tests, result.tests)
    assert removed == ["TestC.test_x"]
    compile(filtered, "filtered", "exec")


def test_sandbox_env_has_no_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The child environment comes from an allowlist and never carries key-like variables (spec rule 1.3)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-never-leak-000000")
    monkeypatch.setenv("SOME_TOKEN", "x")
    env = build_sandbox_env(tmp_path, {"MY_SECRET": "y", "COVERAGE_FILE": "c"})
    assert not any(k in env for k in ("DEEPSEEK_API_KEY", "SOME_TOKEN", "MY_SECRET"))
    assert env["COVERAGE_FILE"] == "c" and env["TEMP"] == str(tmp_path)
    with Workspace() as ws:
        proc = run_process(
            [sys.executable, "-c", "import os; print(os.environ.get('DEEPSEEK_API_KEY', 'absent'))"],
            ws.path, build_sandbox_env(ws.path), 30,
        )
    assert proc.stdout.strip() == "absent"


def test_subject_checks() -> None:
    """C2 rejects bad names, missing entry points, syntax errors and slow imports."""
    with pytest.raises(SubjectError, match="starts with 'test'"):
        load_subject_source(MODULE, "test_mod", "f", check_import=False)
    with pytest.raises(SubjectError, match="no top-level function 'g'"):
        load_subject_source(MODULE, "mod", "g", check_import=False)
    with pytest.raises(SubjectError, match="does not parse"):
        load_subject_source("def f(:\n", "mod", "f", check_import=False)
    with pytest.raises(SubjectError, match="took"):
        load_subject_source(SLOW_IMPORT + MODULE, "mod", "f")
    assert load_subject_source(MODULE, "mod", "f").import_seconds < 1
