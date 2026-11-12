"""Unit tests for the mutation subsystem (C9), milestone 3.

Spec Section 14: each mutation operator, TCE and dedupe, split determinism,
outcome classification for exit codes and timeouts. The last tests are the
milestone 3 acceptance check: a sample function is mutated and run on
Windows, and the obvious mutants are killed.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from fakes import DATA_DIR, STRONG_TESTS
from testroute.mutation.mutant_set import MutantSet, build_mutant_set
from testroute.mutation.operators import generate_mutants
from testroute.mutation.results import KILLED, SET_FEEDBACK, SET_HELDOUT
from testroute.mutation.runner import MutantRunner
from testroute.mutation.tce import DUPLICATE, TCE_EQUIVALENT, classify_static
from testroute.parser import parse_response
from testroute.records import ResultsStore


def _descriptions(source: str, entry: str = "f") -> list[tuple[str, str]]:
    return [(m.operator, m.description) for m in generate_mutants(source, entry)]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(a, b):\n    return a + b\n", ("AOR", "+ -> -")),
        ("def f(a, b):\n    return a * b\n", ("AOR", "* -> /")),
        ("def f(a, b):\n    return a < b\n", ("ROR", "< -> <=")),
        ("def f(a, b):\n    return a == b\n", ("ROR", "== -> !=")),
        ("def f(a, b):\n    return a and b\n", ("COR", "and -> or")),
        ("def f(a):\n    return not a\n", ("COR", "remove not")),
        ("def f(a, b):\n    return a in b\n", ("MEM", "in -> not in")),
        ("def f(a):\n    return a is None\n", ("MEM", "is -> is not")),
        ("def f():\n    return 5\n", ("CON", "5 -> 6")),
        ("def f():\n    return 5\n", ("CON", "5 -> 4")),
        ("def f():\n    return True\n", ("CON", "True -> False")),
        ("def f():\n    return ''\n", ("CON", '"" -> "X"')),
        ("def f(a):\n    a += 1\n    return a\n", ("ASG", "+= -> -=")),
        ("def f(a):\n    return a\n", ("RET", "return value -> None")),
        ("def f(a):\n    return a[1:]\n", ("IDX", "slice lower 1 -> 2")),
        ("def f(a):\n    return a[-1]\n", ("IDX", "index -1 -> -2")),
        ("def f(a):\n    for x in a:\n        break\n", ("BRK", "break -> continue")),
    ],
)
def test_each_operator_family(source: str, expected: tuple[str, str]) -> None:
    """Every operator family of spec 6.1 produces its mutant on a minimal function."""
    assert expected in _descriptions(source)


def test_return_none_and_docstring_untouched() -> None:
    """'return None' is not mutated to itself and the docstring is never a constant mutant."""
    source = 'def f(a):\n    """""" \n    return None\n'
    assert _descriptions(source) == []


def test_index_constants_not_doubled() -> None:
    """A subscript constant is mutated by IDX only, not a second time by CON."""
    families = Counter(op for op, _ in _descriptions("def f(a):\n    return a[2]\n"))
    assert families == Counter({"IDX": 2, "RET": 1})


def test_helpers_are_mutated_but_unrelated_functions_are_not() -> None:
    """Only the entry point and the functions it calls are mutated (spec 6.1)."""
    source = "def helper(x):\n    return x + 1\n\ndef unused(x):\n    return x - 1\n\ndef f(x):\n    return helper(x)\n"
    lines = {m.line for m in generate_mutants(source, "f")}
    assert 2 in lines and 5 not in lines


def test_precedence_kept_by_parentheses() -> None:
    """Replacing an inner operator keeps the original grouping: a - b * c becomes a - (b + c)."""
    mutants = generate_mutants("def f(a, b, c):\n    return a - b * c\n", "f")
    sources = {m.description: m.source for m in mutants}
    assert "a - (b + c)" in sources["* -> +"]


def test_tce_and_duplicates() -> None:
    """A mutant compiling to the original is TCE_EQUIVALENT; two mutants compiling alike are DUPLICATE."""
    original = "def f(a, b):\n    return not (a in b)\n"
    same = "def f(a, b):\n    return (not (a in b))\n"
    first = "def f(a, b):\n    return (a in b)\n"
    folded = "def f(a, b):\n    return not (a not in b)\n"
    labels = classify_static(original, [same, first, folded])
    assert labels == [(TCE_EQUIVALENT, None), ("", None), (DUPLICATE, 1)]


def _set(source: str, entry: str, **kwargs) -> MutantSet:
    return build_mutant_set("fid", "mod", entry, source, **kwargs)


BIG = "def f(xs):\n" + "".join(f"    y{i} = xs[{i}] + {i} * 2 < 3\n" for i in range(12)) + "    return y0\n"


def test_cap_is_stratified_and_recorded() -> None:
    """Above the cap, a seeded stratified sample is kept and dropped ids are recorded (spec 6.3)."""
    full = _set(BIG, "f", cap=1000)
    capped = _set(BIG, "f", cap=20)
    assert len(full.scoring()) > 20 and len(capped.scoring()) == 20
    assert len(capped.capped_out_ids) == len(full.scoring()) - 20
    full_families = Counter(m.operator for m in full.scoring())
    kept_families = Counter(m.operator for m in capped.scoring())
    total = sum(full_families.values())
    # Proportional allocation: each family keeps its share of the cap, rounded
    # by largest remainder, so it is within one of the exact quota.
    for family, count in full_families.items():
        assert abs(kept_families[family] - 20 * count / total) < 1


def test_split_is_deterministic_and_balanced() -> None:
    """The 50/50 split depends only on the seed and is balanced within each family (spec 6.5)."""
    a, b = _set(BIG, "f"), _set(BIG, "f")
    assert [m.set_label for m in a.mutants] == [m.set_label for m in b.mutants]
    for family in {m.operator for m in a.scoring()}:
        labels = Counter(m.set_label for m in a.scoring() if m.operator == family)
        assert abs(labels[SET_FEEDBACK] - labels[SET_HELDOUT]) <= 1
    other = _set(BIG, "f", split_seed=8)
    assert [m.set_label for m in other.mutants] != [m.set_label for m in a.mutants]


def test_mutant_set_round_trip(tmp_path: Path) -> None:
    """A saved mutant file loads back unchanged (plain JSON, spec 6.4 step 4)."""
    original = _set(BIG, "f")
    original.save(tmp_path / "fid.json")
    loaded = MutantSet.load(tmp_path / "fid.json")
    assert loaded == original


COUNT = "def count(n):\n    i = 0\n    while i < n:\n        i += 1\n    return i\n"
COUNT_TESTS = "from mod import count\n\ndef test_count():\n    assert count(3) == 3\n"


def _runner(mutant_set: MutantSet, tmp_path: Path, **kwargs) -> MutantRunner:
    return MutantRunner(mutant_set, ResultsStore(tmp_path), **kwargs)


def test_outcome_classes_and_timeout_diagnosis(tmp_path: Path) -> None:
    """KILLED, SURVIVED_NOT_COVERED and TIMEOUT with both diagnoses (spec 6.6, FB3).

    In count(), 'i += 1' to 'i -= 1' never terminates for n = 3. Marked
    DIFF_TIMEOUT_ONLY it is 'likely_genuine'; unmarked it is 'unexplained'.
    """
    mutant_set = _set(COUNT, "count")
    hang = next(m for m in mutant_set.scoring() if m.description == "+= -> -=")
    for marked, diagnosis in ((True, "likely_genuine"), (False, "unexplained")):
        labels = {hang.mutant_id: ("DIFF_TIMEOUT_ONLY", "NOT_REVIEWED")} if marked else None
        mutant_set = _set(COUNT, "count", equivalence=labels)
        runner = _runner(mutant_set, tmp_path, min_limit_s=1.0, multiplier=1.0)
        results = {r.mutant_id: r for r in runner.run_all(COUNT_TESTS, {1, 2, 3, 4, 5}, 0.1)}
        assert results[hang.mutant_id].outcome == "TIMEOUT"
        assert results[hang.mutant_id].timeout_diagnosis == diagnosis
    killed = [r for r in results.values() if r.outcome == KILLED]
    assert killed, "the obvious mutants of count() must be killed"
    not_covered = {r.outcome for r in runner.run_all(COUNT_TESTS, {1}, 0.1)}
    assert not_covered == {"SURVIVED_NOT_COVERED"}


def test_exit_code_mapping(tmp_path: Path) -> None:
    """Exit 0 is SURVIVED and a file collecting no tests (exit 5) is ENV_ERROR after one retry."""
    mutant_set = _set(COUNT, "count")
    runner = _runner(mutant_set, tmp_path)
    weak = "from mod import count\n\ndef test_runs():\n    assert count(0) == 0 or True\n"
    assert {r.outcome for r in runner.run_all(weak, {1, 2, 3, 4, 5}, 0.1)} == {"SURVIVED"}
    empty = "from mod import count\n"
    assert {r.outcome for r in runner.run_all(empty, {1, 2, 3, 4, 5}, 0.1)} == {"ENV_ERROR"}
    assert runner.env_errors


def test_time_limit_formula(tmp_path: Path) -> None:
    """The mutant limit is max(3 s, 5 x baseline) (spec 6.6)."""
    runner = _runner(_set(COUNT, "count"), tmp_path)
    assert runner.time_limit(0.1) == 3.0 and runner.time_limit(2.0) == 10.0


def test_sample_function_obvious_mutants_killed(tmp_path: Path) -> None:
    """Milestone 3 acceptance: every mutant of classify() is killed by a strong suite on Windows."""
    source = (DATA_DIR / "classify.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    mutant_set = build_mutant_set("tiny_classify", "classify", "classify", source)
    tests = parse_response(STRONG_TESTS).code
    runner = MutantRunner(mutant_set, ResultsStore(tmp_path))
    results = runner.run_all(tests, set(range(1, 15)), 0.5)
    assert len(results) >= 8
    survivors = [r.mutant_id for r in results if r.outcome != KILLED]
    assert survivors == [], survivors
