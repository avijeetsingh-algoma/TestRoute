"""Unit tests for the Response Parser (C6) and the Static Validator (C7).

Spec Section 14 asks for parser cases and every static check; the cases follow
D2 Table 4 and spec C6 and C7.
"""

from __future__ import annotations

import pytest

from testroute.llm.fake import canned
from testroute.parser import parse_response
from testroute.static_check import check_test_file

MOD, ENTRY = "classify", "classify"
HEADER = f"from {MOD} import {ENTRY}\n"


def _text(reply) -> str:
    return reply[0] if isinstance(reply, tuple) else reply


def test_parser_takes_fenced_block_and_reports_prose() -> None:
    """Prose around a fenced block is stripped and the amount is reported."""
    result = parse_response(_text(canned("prose_around", MOD, ENTRY)))
    assert result.code is not None and result.code.startswith(HEADER)
    assert result.report["stripped_prefix_chars"] > 0 and result.report["stripped_suffix_chars"] > 0


def test_parser_prefers_largest_block_with_a_test() -> None:
    """With several blocks, the largest one defining a test_ function wins over a larger helper block."""
    helper = "```python\n" + "x = 1\n" * 40 + "```\n"
    tests = f"```python\n{HEADER}def test_a():\n    assert True\n```\n"
    small_tests = f"```python\n{HEADER}def test_b():\n    assert 1\n```\n"
    result = parse_response(helper + tests + small_tests)
    assert "def test_a" in (result.code or "")


def test_parser_accepts_unfenced_valid_python() -> None:
    """Without a fence the whole text is accepted only if it parses."""
    assert parse_response(f"{HEADER}def test_a():\n    assert True\n").code is not None
    assert parse_response("Sure! def test_a(: nope").error_type == "NO_CODE"


def test_parser_no_code_and_empty() -> None:
    """A refusal and an empty reply are both NO_CODE."""
    assert parse_response(_text(canned("no_code", MOD, ENTRY))).error_type == "NO_CODE"
    assert parse_response("   ").error_type == "NO_CODE"


def test_parser_unclosed_fence_runs_to_end() -> None:
    """A reply cut off inside its code block still yields the partial code (it then fails V2)."""
    result = parse_response(_text(canned("truncated", MOD, ENTRY)))
    assert result.code is not None and result.report["chosen_block_closed"] is False
    assert check_test_file(result.code, MOD, ENTRY).error_type == "SYNTAX_ERROR"


def test_parser_ignores_non_python_fences() -> None:
    """A fenced block tagged with another language is not taken as Python."""
    result = parse_response("```bash\npytest -q\n```\n")
    assert result.error_type == "NO_CODE"


def _check(body: str):
    return check_test_file(body, MOD, ENTRY)


def test_static_ok() -> None:
    """A well-formed file passes every check and its tests are listed."""
    result = _check(f"import pytest\nimport math\n{HEADER}\ndef test_a():\n    assert math.isfinite(classify(1) and 1)\n")
    assert result.ok and result.test_names == ["test_a"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (f"{HEADER}def test_a(:\n    assert True\n", "SYNTAX_ERROR"),
        ("from other import classify\ndef test_a():\n    assert True\n", "WRONG_IMPORT"),
        (f"import numpy\n{HEADER}def test_a():\n    assert True\n", "DISALLOWED_IMPORT"),
        (f"{HEADER}def helper():\n    assert True\n", "NO_TESTS"),
        (f"{HEADER}def test_a():\n    classify(1)\n", "NO_ASSERT"),
        (f"import os\n{HEADER}def test_a():\n    assert True\n", "UNSAFE"),
        (f"{HEADER}def test_a():\n    assert eval('1')\n", "UNSAFE"),
        (f"{HEADER}def test_a():\n    assert open\n", "UNSAFE"),
        (f"from subprocess import run\n{HEADER}def test_a():\n    assert True\n", "UNSAFE"),
        (f"{HEADER}def test_a():\n    assert ().__class__.__subclasses__()\n", "UNSAFE"),
    ],
)
def test_static_error_types(body: str, expected: str) -> None:
    """Each rule of spec C7 produces its own error type."""
    assert _check(body).error_type == expected


def test_static_first_failure_follows_spec_order() -> None:
    """With several violations, the recorded type is the first in spec order (WRONG_IMPORT before UNSAFE)."""
    result = _check("import os\ndef test_a():\n    assert True\n")
    assert result.error_type == "WRONG_IMPORT"
    assert {t for t, _ in result.violations} == {"WRONG_IMPORT", "UNSAFE"}


def test_static_accepts_pytest_raises_and_test_classes() -> None:
    """pytest.raises counts as an assertion, also via 'from pytest import raises', and Test classes are tests."""
    body = (
        f"from pytest import raises\nimport pytest\n{HEADER}\n"
        "class TestC:\n    def test_x(self):\n        with raises(TypeError):\n            classify(None < 1)\n"
        "def test_y():\n    with pytest.raises(TypeError):\n        classify(None < 1)\n"
    )
    result = _check(body)
    assert result.ok and result.test_names == ["TestC.test_x", "test_y"]


def test_static_unsafe_canned_reply() -> None:
    """The fake provider's unsafe reply is rejected before it can run."""
    code = parse_response(_text(canned("unsafe_import", MOD, ENTRY))).code
    assert _check(code).error_type == "UNSAFE"
