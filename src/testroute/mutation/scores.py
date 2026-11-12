"""Mutation score formulas.

Component: C9 (Mutation Analyzer), scoring part. Also used by C11 for run
records and by C12 for analysis.

Responsibility:
    Computes the four scores of spec Section 6.7 for one mutant set S, with
    E = mutants labelled EQUIVALENT:

    * strict MS (primary)  = KILLED / (|S| - |E|) x 100
    * lenient MS           = (KILLED + TIMEOUT) / (|S| - |E|) x 100
    * raw MS               = KILLED / |S| x 100   (before equivalence removal)
    * UNSURE-excluded MS   = strict MS with UNSURE mutants also removed from
                             the denominator

    ENV_ERROR mutants are removed from S altogether for that attempt (spec
    Section 6.6) and counted separately. A score with a zero denominator is
    None ("not defined") rather than 0 or 100, so callers must decide what an
    empty set means instead of inheriting a silent default.

    Equivalent mutants are excluded from the denominator only. A suite cannot
    kill a truly equivalent mutant; if one is ever reported KILLED (which
    would mean the review was wrong) the kill is not counted in strict or
    lenient MS, so the score can never exceed 100.

Receives: mutant results and a mapping of mutant id to equivalence label.
Produces: :class:`SetScores`.
Talks to: used by ``mutation.results``, C11 and C12.
Spec sections: 6.6, 6.7.
Feedback IDs: FB2 (equivalents leave the denominator; UNSURE sensitivity),
    FB3 (strict vs lenient handling of timeouts).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol


class _ResultLike(Protocol):
    mutant_id: str
    outcome: str
    timeout_diagnosis: str


@dataclass(frozen=True)
class SetScores:
    """Scores and counts for one mutant set.

    Attributes:
        strict: Strict MS in percent, or None if undefined.
        lenient: Lenient MS in percent, or None.
        raw: Raw MS in percent, or None.
        unsure_excluded: Strict MS without UNSURE mutants, or None.
        size: |S| after removing ENV_ERROR mutants.
        equivalent: Number of EQUIVALENT mutants in S.
        unsure: Number of UNSURE mutants in S.
        counts: Number of mutants per outcome (ENV_ERROR included).
        timeouts_by_diagnosis: TIMEOUT counts per timeout_diagnosis value.
    """

    strict: float | None
    lenient: float | None
    raw: float | None
    unsure_excluded: float | None
    size: int
    equivalent: int
    unsure: int
    counts: dict[str, int] = field(default_factory=dict)
    timeouts_by_diagnosis: dict[str, int] = field(default_factory=dict)


def _percent(numerator: int, denominator: int) -> float | None:
    """numerator / denominator x 100, rounded to 2 places; None if denominator is 0."""
    return round(100.0 * numerator / denominator, 2) if denominator > 0 else None


def compute_scores(results: Iterable[_ResultLike], equivalence: Mapping[str, str]) -> SetScores:
    """Compute spec 6.7 scores for one set of mutant results.

    Args:
        results: Results of the mutants in the set, one per mutant.
        equivalence: Mutant id to equivalence label; ids not present are
            treated as not equivalent.

    Returns:
        A :class:`SetScores`.

    Raises:
        Nothing.

    Side effects:
        None.
    """
    results = list(results)
    counts = Counter(r.outcome for r in results)
    scored = [r for r in results if r.outcome != "ENV_ERROR"]
    labels = {r.mutant_id: equivalence.get(r.mutant_id, "") for r in scored}

    size = len(scored)
    equivalent_ids = {m for m, label in labels.items() if label == "EQUIVALENT"}
    unsure_ids = {m for m, label in labels.items() if label == "UNSURE"}

    non_equivalent = [r for r in scored if r.mutant_id not in equivalent_ids]
    killed = sum(1 for r in non_equivalent if r.outcome == "KILLED")
    timeouts = sum(1 for r in non_equivalent if r.outcome == "TIMEOUT")
    killed_raw = sum(1 for r in scored if r.outcome == "KILLED")
    sure = [r for r in non_equivalent if r.mutant_id not in unsure_ids]
    killed_sure = sum(1 for r in sure if r.outcome == "KILLED")

    timeout_diag = Counter(r.timeout_diagnosis or "unexplained" for r in scored if r.outcome == "TIMEOUT")
    return SetScores(
        strict=_percent(killed, len(non_equivalent)),
        lenient=_percent(killed + timeouts, len(non_equivalent)),
        raw=_percent(killed_raw, size),
        unsure_excluded=_percent(killed_sure, len(sure)),
        size=size,
        equivalent=len(equivalent_ids),
        unsure=len(unsure_ids),
        counts={outcome: counts.get(outcome, 0) for outcome in ("KILLED", "SURVIVED", "SURVIVED_NOT_COVERED", "TIMEOUT", "ENV_ERROR")},
        timeouts_by_diagnosis=dict(timeout_diag),
    )
