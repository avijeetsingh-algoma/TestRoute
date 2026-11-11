"""Unit tests for the Prompt Builder (C3) and the Model Router (C4).

Covers prompt filling (literal, single pass, blocks only when enabled),
the truncation order of spec C3, held-out rejection in the bundle, and the
routing policies of spec C4 (spec Section 14).
"""

from __future__ import annotations

import pytest

from fakes import DATA_DIR
from testroute.mutation.results import SET_FEEDBACK, SET_HELDOUT, HeldOutLeakError, MutantInfo
from testroute.prompts import COV, EXEC, MUT, FailureLine, FeedbackBundle, PromptBuilder, fill
from testroute.router import CLOUD, LOCAL, ConditionError, Router, parse_condition
from testroute.subjects import load_subject_source

SOURCE = (DATA_DIR / "classify.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def subject():
    """Subject without the import subprocess (only prompt text is tested here)."""
    return load_subject_source(SOURCE, "classify", "classify", check_import=False)


@pytest.fixture(scope="module")
def builder() -> PromptBuilder:
    """Builder reading the real templates in prompts/."""
    return PromptBuilder()


def _mutant(k: int, set_label: str = SET_FEEDBACK) -> MutantInfo:
    return MutantInfo(f"M{k}", "ROR", 10, "    if n < 0:", f"    if n < {k}:", set_label)


def test_fill_is_literal_single_pass() -> None:
    """Inserted text containing braces or other placeholder names is never substituted again."""
    out = fill("{A} and {B}", {"A": "{B} {x}", "B": "b"})
    assert out == "{B} {x} and b"


def test_generation_prompt_uses_template_verbatim(subject, builder) -> None:
    """T1 contains the module source and the fixed requirement lines from the D2 template."""
    prompt = builder.generation_prompt(subject)
    assert prompt.template_id == "T1"
    assert "from classify import classify" in prompt.user
    assert 'return "positive"' in prompt.user
    assert prompt.system.startswith("You are an expert Python test engineer.")
    assert len(prompt.template_hash) == 64 and len(prompt.sha256) == 64


def test_disabled_feedback_types_never_appear(subject, builder) -> None:
    """An F1 condition shows the EXEC block only, even when the bundle has coverage and mutants."""
    bundle = FeedbackBundle(
        failures=(FailureLine("test_a", "AssertionError", "assert 1 == 2"),),
        uncovered_lines=((12, "    if n == 0:"),),
        surviving_mutants=(_mutant(1),),
    )
    prompt = builder.feedback_prompt(subject, "def test_a(): pass", bundle, frozenset({EXEC}))
    assert prompt.blocks == (EXEC,)
    assert "test_a: AssertionError: assert 1 == 2" in prompt.user
    assert "not executed by any test" not in prompt.user and "Change 1" not in prompt.user


def test_at_most_five_mutants(subject, builder) -> None:
    """The MUT block shows at most 5 mutants (spec Section 7)."""
    bundle = FeedbackBundle(surviving_mutants=tuple(_mutant(k) for k in range(1, 9)))
    prompt = builder.feedback_prompt(subject, None, bundle, frozenset({EXEC, MUT}))
    assert "Change 5:" in prompt.user and "Change 6:" not in prompt.user
    assert "# no test file was returned" in prompt.user


def test_heldout_mutant_rejected_by_bundle() -> None:
    """A held-out mutant cannot even be placed in a feedback bundle (FB11)."""
    with pytest.raises(HeldOutLeakError):
        FeedbackBundle(surviving_mutants=(_mutant(1, SET_HELDOUT),))


def test_truncation_order(subject) -> None:
    """Over budget, feedback shrinks in the fixed order: mutants, then error messages, then coverage lines."""
    tight = PromptBuilder(budget_tokens=1)
    bundle = FeedbackBundle(
        failures=(FailureLine("test_a", "AssertionError", "x" * 300),),
        uncovered_lines=tuple((n, "    pass") for n in range(10, 14)),
        surviving_mutants=tuple(_mutant(k) for k in range(1, 6)),
    )
    prompt = tight.feedback_prompt(subject, "def test_a(): pass", bundle, frozenset({EXEC, COV, MUT}))
    steps = list(prompt.shortening)
    assert steps[:4] == [f"mutants reduced to {n}" for n in (4, 3, 2, 1)]
    assert steps[4:6] == ["error messages cut to 120 characters", "error messages cut to 40 characters"]
    assert steps[6] == "4 coverage lines removed"
    assert prompt.over_budget


def test_exit_branch_rendering(subject, builder) -> None:
    """A branch to a negative line (function exit in coverage.py) is written 'to exit'."""
    bundle = FeedbackBundle(missed_branches=((10, -8), (12, 13)))
    prompt = builder.feedback_prompt(subject, "x", bundle, frozenset({EXEC, COV}))
    assert "branch from line 10 to exit is never taken" in prompt.user
    assert "branch from line 12 to line 13 is never taken" in prompt.user


@pytest.mark.parametrize(
    ("name", "calls", "roles"),
    [
        ("L-F0", 1, [LOCAL]),
        ("C-F0", 1, [CLOUD]),
        ("L-F4", 4, [LOCAL] * 4),
        ("C-F2", 4, [CLOUD] * 4),
        ("ESC", 4, [LOCAL, LOCAL, CLOUD, CLOUD]),
    ],
)
def test_router_policies(name: str, calls: int, roles: list[str]) -> None:
    """Each policy routes every call of its budget to the model of spec C4."""
    condition = parse_condition(name)
    router = Router(condition)
    assert condition.max_calls == calls
    assert [router.role_for_call(k) for k in range(1, calls + 1)] == roles
    with pytest.raises(ValueError):
        router.role_for_call(calls + 1)


def test_router_escalation_point_and_feedback_types() -> None:
    """ESC escalates exactly at call 3 and uses F4 feedback; F3 is EXEC plus MUT."""
    router = Router(parse_condition("esc"))
    assert [router.is_escalation(k) for k in range(1, 5)] == [False, False, True, False]
    assert parse_condition("ESC").feedback_types == frozenset({EXEC, COV, MUT})
    assert parse_condition("C-F3").feedback_types == frozenset({EXEC, MUT})


def test_unknown_condition() -> None:
    """A misspelled condition is rejected with a clear error."""
    with pytest.raises(ConditionError):
        parse_condition("X-F9")
