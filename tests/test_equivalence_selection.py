"""Unit tests for subject screening (spec 9.1) and the equivalence pipeline (spec 6.4).

Covers decision-point counting, the static I/O screen, the seeded band draw,
differential execution labels (DIFF_OUTPUT, DIFF_TIMEOUT_ONLY, NO_DIFF) with
float tolerance, and the rule that the review CSV is created with empty labels
and existing labels are never overwritten.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from testroute import equivalence
from testroute.differential import DIFF_OUTPUT, DIFF_TIMEOUT_ONLY, NO_DIFF, compare, execute, same_output
from testroute.mutation.mutant_set import build_mutant_set
from testroute.subject_selection import Screening, count_decision_points, draw, uses_io_clock_random


def test_decision_points() -> None:
    """if/elif, loops, extra boolean operands and comprehension conditions each count once."""
    source = (
        "def f(xs):\n"
        "    if xs and len(xs) > 1:\n        return 1\n"
        "    elif not xs:\n        return 0\n"
        "    for x in xs:\n        pass\n"
        "    return [y for y in xs if y]\n"
    )
    assert count_decision_points(source, "f") == 5


def test_io_clock_random_screen() -> None:
    """Imports of random or time and calls of open are rejected; math is fine."""
    assert uses_io_clock_random("import random\ndef f():\n    return random.random()\n")
    assert uses_io_clock_random("def f():\n    return open('x')\n")
    assert not uses_io_clock_random("import math\ndef f(x):\n    return math.sqrt(x)\n")


def _screenings(n: int) -> list[Screening]:
    rows = []
    for k in range(n):
        s = Screening(f"HumanEval/{k}", f"HumanEval_{k}", "f", mutants_after_tce=10 + k)
        s.checks = {c: "pass" for c in (
            "parses", "no_io_clock_random", "max_80_lines", "min_2_decisions", "import_under_1s",
            "passes_humaneval_tests", "deterministic", "min_10_mutants")}
        rows.append(s)
    return rows


def test_draw_is_seeded_stratified_and_sets_pilot() -> None:
    """The draw picks 35 across three equal bands, 5 of them pilot, identically for the same seed."""
    a, b = _screenings(90), _screenings(90)
    draw(a)
    draw(b)
    assert [(s.band, s.group) for s in a] == [(s.band, s.group) for s in b]
    groups = [s.group for s in a if s.group]
    assert len(groups) == 35 and groups.count("pilot") == 5
    for band in ("easy", "medium", "hard"):
        assert 11 <= sum(1 for s in a if s.group and s.band == band) <= 12
        assert sum(1 for s in a if s.group == "pilot" and s.band == band) >= 1
    other = _screenings(90)
    draw(other, seed=1)
    assert [s.group for s in other] != [s.group for s in a]


def test_draw_needs_35_eligible() -> None:
    """Fewer than 35 eligible problems is an error, not a smaller sample."""
    with pytest.raises(ValueError):
        draw(_screenings(20))


def test_float_tolerance() -> None:
    """Floats within 1e-6 are equal; types must still match."""
    assert same_output({"f": 0.1 + 0.2}, {"f": 0.3})
    assert not same_output({"f": 0.3}, {"f": 0.31})
    assert not same_output(1, True)


ORIGINAL = "def f(n):\n    return n * 2\n"


def _label(mutant_source: str, inputs: list) -> str:
    original = execute("mod", "f", ORIGINAL, inputs)
    return compare(original, execute("mod", "f", mutant_source, inputs, per_input_timeout_s=0.5, max_timeouts=1)).label


def test_differential_labels() -> None:
    """Same outputs give NO_DIFF, a different output DIFF_OUTPUT, a hang alone DIFF_TIMEOUT_ONLY (spec 6.4)."""
    inputs = [[0], [1], [5]]
    assert _label("def f(n):\n    return n + n\n", inputs) == NO_DIFF
    assert _label("def f(n):\n    return n * 3\n", inputs) == DIFF_OUTPUT
    hang = "def f(n):\n    while n > 4:\n        pass\n    return n * 2\n"
    assert _label(hang, inputs) == DIFF_TIMEOUT_ONLY


def test_exception_type_is_an_output() -> None:
    """A different exception type is a difference; the same type is not."""
    inputs = [["x"]]
    original = execute("mod", "f", "def f(s):\n    return int(s)\n", inputs)
    same = execute("mod", "f", "def f(s):\n    return int(s) + 0\n", inputs)
    other = execute("mod", "f", "def f(s):\n    return s[5]\n", inputs)
    assert compare(original, same).label == NO_DIFF
    assert compare(original, other).label == DIFF_OUTPUT


@pytest.fixture
def tiny_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Point the review step at one small subject whose mutants all have NO_DIFF verdicts."""
    source = "def f(a, b):\n    if a < b:\n        return a\n    return b\n"
    mutant_set = build_mutant_set("tiny", "tiny", "f", source)
    verdicts = {m.mutant_id: {"label": NO_DIFF, "inputs_compared": 3, "equal_exceptions": 0} for m in mutant_set.mutants}

    class _Problem:
        function_id, task_id, entry_point = "tiny", "Tiny/0", "f"

    _Problem.source = source
    monkeypatch.setattr(equivalence, "selected_subjects", lambda: [{"function_id": "tiny", "group": "main"}])
    monkeypatch.setattr(equivalence, "_load_differential",
                        lambda fid: {"inputs_usable": 3, "inputs_total": 3, "mutants": verdicts})
    monkeypatch.setattr(equivalence, "_mutants_of", lambda fid: (_Problem, mutant_set))
    return mutant_set


def test_review_csv_empty_labels_and_never_overwritten(tiny_pipeline, tmp_path: Path, monkeypatch) -> None:
    """The review CSV starts with empty label columns; a rerun keeps labels already entered by the reviewers."""
    monkeypatch.setattr(equivalence.paths, "EQUIVALENCE_DIR", tmp_path)
    review = tmp_path / "equivalence_review.csv"
    first = equivalence.review_equivalence(review)
    rows = list(csv.DictReader(review.open(encoding="utf-8")))
    assert first.rows_added == len(rows) > 0
    assert all(r["reviewer_1"] == r["reviewer_2"] == r["final_label"] == r["justification"] == "" for r in rows)
    assert (tmp_path / "review_tiny.md").is_file()

    rows[0].update(reviewer_1="EQUIVALENT", reviewer_2="UNSURE", final_label="UNSURE", justification="discussed")
    with review.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=equivalence.REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    second = equivalence.review_equivalence(review)
    again = list(csv.DictReader(review.open(encoding="utf-8")))
    assert second.rows_added == 0
    assert again[0]["final_label"] == "UNSURE" and again[0]["justification"] == "discussed"


def test_finalize_refuses_incomplete_review(tiny_pipeline, tmp_path: Path, monkeypatch) -> None:
    """Final mutant files are not written while any NO_DIFF mutant lacks a final label."""
    monkeypatch.setattr(equivalence.paths, "EQUIVALENCE_DIR", tmp_path)
    review = tmp_path / "equivalence_review.csv"
    equivalence.review_equivalence(review)
    with pytest.raises(equivalence.ReviewIncomplete, match="final_label missing"):
        equivalence.finalize_mutants(review)
