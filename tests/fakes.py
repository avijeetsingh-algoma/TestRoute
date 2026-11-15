"""Test doubles shared by the offline tests (not a test module itself).

ScriptedAnalyzer stands in for the milestone 3 mutation analyzer. It
implements the same MutationAnalyzer protocol and the same isolation rule:
complete results (both sets) go to the sink, only the feedback view is
returned. A mutant counts as KILLED when the filtered suite contains its
"killer" text, which makes outcomes predictable without running mutants.
"""

from __future__ import annotations

from pathlib import Path

from testroute.mutation.results import (
    KILLED,
    SET_FEEDBACK,
    SET_HELDOUT,
    SURVIVED,
    SURVIVED_NOT_COVERED,
    FeedbackMutationView,
    MutantInfo,
    MutantResult,
    MutationSink,
)

DATA_DIR = Path(__file__).parent / "data"

# Mutants of tests/data/classify.py. Line numbers refer to that file.
# Each entry: (MutantInfo, killer text that a killing suite must contain).
CLASSIFY_MUTANTS: list[tuple[MutantInfo, str]] = [
    (MutantInfo("ROR_0001", "ROR", 10, "    if n < 0:", "    if n <= 0:", SET_FEEDBACK), "classify(0)"),
    (MutantInfo("ROR_0002", "ROR", 10, "    if n < 0:", "    if n < -1:", SET_HELDOUT), "classify(-1)"),
    (MutantInfo("ROR_0003", "ROR", 12, "    if n == 0:", "    if n != 0:", SET_FEEDBACK), '"zero"'),
    (MutantInfo("RET_0004", "RET", 14, '    return "positive"', "    return None", SET_HELDOUT), '"positive"'),
]

# Mutated line of held-out mutant RET_0004, which survives the weak suites and
# so would appear in a prompt if held-out data ever leaked into feedback.
HELDOUT_MARKER = "return None"


class ScriptedAnalyzer:
    """MutationAnalyzer whose outcomes depend on text in the test suite."""

    def __init__(self, sink: MutationSink, mutants: list[tuple[MutantInfo, str]] | None = None) -> None:
        self.sink = sink
        self.mutants = mutants if mutants is not None else CLASSIFY_MUTANTS
        self.info = {m.mutant_id: m for m, _ in self.mutants}

    def _results(self, tests: str, covered: set[int]) -> list[MutantResult]:
        results = []
        for mutant, killer in self.mutants:
            if mutant.line not in covered:
                outcome = SURVIVED_NOT_COVERED
            else:
                outcome = KILLED if killer in tests else SURVIVED
            results.append(MutantResult(mutant.mutant_id, mutant.set_label, outcome, 0.01))
        return results

    def analyze_attempt(self, run_id, attempt_no, filtered_tests, covered_lines, baseline_s) -> FeedbackMutationView:
        results = self._results(filtered_tests, covered_lines)
        self.sink.record_attempt_mutants(run_id, attempt_no, results, self.info)
        feedback = tuple(r for r in results if r.set_label == SET_FEEDBACK)
        return FeedbackMutationView(
            results=feedback,
            mutants={m: i for m, i in self.info.items() if i.set_label == SET_FEEDBACK},
            prompt_order=tuple(sorted(r.mutant_id for r in feedback)),
        )

    def analyze_final(self, run_id, filtered_tests, covered_lines, baseline_s) -> None:
        self.sink.record_final_mutants(run_id, self._results(filtered_tests, covered_lines), self.info)

    def reuse_attempt_as_final(self, run_id, attempt_no) -> None:
        self.sink.reuse_attempt_as_final(run_id, attempt_no)


def fenced(code: str) -> str:
    """Wrap test code in a python fence as a model reply would."""
    return f"```python\n{code.strip()}\n```\n"


# Replies used by the integration tests.
WEAK_TESTS = fenced(
    '''
from classify import classify

def test_negative():
    assert classify(-1) == "negative"

def test_wrong():
    assert classify(5) == "negative"
'''
)

NEGATIVE_ONLY_TESTS = fenced(
    '''
from classify import classify

def test_negative():
    assert classify(-1) == "negative"
'''
)

STRONG_TESTS = fenced(
    '''
import pytest
from classify import classify

def test_negative():
    assert classify(-1) == "negative"

def test_zero():
    assert classify(0) == "zero"

@pytest.mark.parametrize("n", [1, 5])
def test_positive(n):
    assert classify(n) == "positive"
'''
)
