"""Mutant result data classes and the held-out isolation boundary.

Component: C9 (Mutation Analyzer), interface part.

Responsibility:
    Defines what mutation analysis returns, and makes it structurally
    impossible for held-out results to reach the Loop Controller (C10) or a
    prompt (C3), as required by spec Section 5 and FB11:

    * :class:`FeedbackMutationView` is the ONLY mutation object the controller
      receives. Its constructor raises :class:`HeldOutLeakError` if any result
      or mutant in it belongs to another set.
    * Full results (both sets) never pass through the controller: an analyzer
      sends them to a :class:`MutationSink` (implemented by the records module,
      C11) and returns only the feedback view.
    * :class:`MutationAnalyzer` is the protocol the controller depends on.
      The real AST-based analyzer arrives in milestone 3; the test suite uses
      a scripted analyzer that implements the same protocol.

Receives: per-mutant outcomes from an analyzer.
Produces: :class:`FeedbackMutationView`; full results go to a sink.
Talks to: C10 (receives the view), C11 (sink), ``scores`` (formulas).
Spec sections: 5 (isolation by data classes), 6.5 (sets), 6.6 (outcome
    classes), 6.7 (scores).
Feedback IDs: FB3 (TIMEOUT is its own outcome), FB11 (held-out isolation).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

from testroute.mutation import scores as scoring

# Set labels (spec Section 6.5).
SET_FEEDBACK = "feedback"
SET_HELDOUT = "heldout"

# Outcome classes (spec Section 6.6).
KILLED = "KILLED"
SURVIVED = "SURVIVED"
SURVIVED_NOT_COVERED = "SURVIVED_NOT_COVERED"
TIMEOUT = "TIMEOUT"
ENV_ERROR = "ENV_ERROR"
OUTCOMES: tuple[str, ...] = (KILLED, SURVIVED, SURVIVED_NOT_COVERED, TIMEOUT, ENV_ERROR)

# Equivalence labels after the review of spec Section 6.4. NOT_REVIEWED marks
# mutants that never needed review (DIFF_OUTPUT or DIFF_TIMEOUT_ONLY).
EQUIVALENT = "EQUIVALENT"
NOT_EQUIVALENT = "NOT_EQUIVALENT"
UNSURE = "UNSURE"
NOT_REVIEWED = "NOT_REVIEWED"


class HeldOutLeakError(Exception):
    """Raised when held-out data is about to enter the feedback path."""


@dataclass(frozen=True)
class MutantInfo:
    """Static description of one scoring mutant.

    Attributes:
        mutant_id: Stable id within the function, for example ``ROR_0007``.
        operator: Operator family (AOR, ROR, COR, ...).
        line: 1-based line of the change in the module.
        original_line: Source line before mutation.
        mutated_line: Source line after mutation.
        set_label: ``feedback`` or ``heldout``.
        equivalence: Final equivalence label (EQUIVALENT, NOT_EQUIVALENT,
            UNSURE or NOT_REVIEWED).
        diff_timeout_only: True when differential execution (6.4) found the
            only difference to be non-termination; used for timeout_diagnosis.
    """

    mutant_id: str
    operator: str
    line: int
    original_line: str
    mutated_line: str
    set_label: str
    equivalence: str = NOT_REVIEWED
    diff_timeout_only: bool = False


@dataclass(frozen=True)
class MutantResult:
    """Outcome of one mutant against one test suite.

    Attributes:
        mutant_id: Id of the mutant.
        set_label: ``feedback`` or ``heldout``.
        outcome: One of OUTCOMES.
        duration_s: Execution time (0 when not executed).
        timeout_diagnosis: For TIMEOUT: ``likely_genuine`` or
            ``unexplained``; empty otherwise (spec Section 6.6).
    """

    mutant_id: str
    set_label: str
    outcome: str
    duration_s: float = 0.0
    timeout_diagnosis: str = ""


@dataclass(frozen=True)
class FeedbackMutationView:
    """Feedback-set results of one attempt: everything the controller may see.

    Attributes:
        results: Results of feedback-set mutants only.
        mutants: Descriptions of the same mutants, keyed by id.
        prompt_order: Feedback mutant ids in the fixed seeded order used to
            choose which survivors appear in a prompt (spec Section 7).

    Raises:
        HeldOutLeakError: from the constructor if any entry is not from the
            feedback set.
    """

    results: tuple[MutantResult, ...]
    mutants: dict[str, MutantInfo] = field(default_factory=dict)
    prompt_order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        leaked = [r.mutant_id for r in self.results if r.set_label != SET_FEEDBACK]
        leaked += [m.mutant_id for m in self.mutants.values() if m.set_label != SET_FEEDBACK]
        if leaked:
            raise HeldOutLeakError(f"non-feedback mutants passed to the feedback view: {sorted(set(leaked))}")

    def scores(self) -> scoring.SetScores:
        """All spec 6.7 scores of the feedback set."""
        return scoring.compute_scores(self.results, {m.mutant_id: m.equivalence for m in self.mutants.values()})

    @property
    def strict_ms(self) -> float | None:
        """Strict feedback-set MS, used for the target and suite ranking (6.7)."""
        return self.scores().strict

    def survivors_for_prompt(self, limit: int) -> list[MutantInfo]:
        """Surviving mutants to show in the MUT block, at most ``limit``.

        SURVIVED and SURVIVED_NOT_COVERED count as survivors. TIMEOUT and
        ENV_ERROR never do (spec Section 7), and confirmed EQUIVALENT mutants
        are skipped because no test can kill them.
        """
        surviving = {r.mutant_id for r in self.results if r.outcome in (SURVIVED, SURVIVED_NOT_COVERED)}
        chosen: list[MutantInfo] = []
        for mutant_id in self.prompt_order or tuple(sorted(surviving)):
            info = self.mutants.get(mutant_id)
            if mutant_id in surviving and info is not None and info.equivalence != EQUIVALENT:
                chosen.append(info)
            if len(chosen) >= limit:
                break
        return chosen

    def to_dict(self) -> dict:
        """JSON-ready form, written to the trace as 09_C9_mutation_feedback_view.json."""
        scores = self.scores()
        return {
            "set": SET_FEEDBACK,
            "scores": asdict(scores),
            "results": [asdict(r) for r in self.results],
        }


class MutationSink(Protocol):
    """Receiver of complete mutation results (both sets). Implemented by C11."""

    def record_attempt_mutants(
        self, run_id: str, attempt_no: int, results: list[MutantResult], mutants: dict[str, MutantInfo]
    ) -> None:
        """Store all results of one attempt (feedback and held-out)."""

    def record_final_mutants(self, run_id: str, results: list[MutantResult], mutants: dict[str, MutantInfo]) -> None:
        """Store all results of the final suite of a run."""

    def reuse_attempt_as_final(self, run_id: str, attempt_no: int) -> None:
        """Mark an attempt's stored results as the final results (suite unchanged)."""


class MutationAnalyzer(Protocol):
    """What the controller needs from mutation analysis (spec Section 6.6)."""

    def analyze_attempt(
        self, run_id: str, attempt_no: int, filtered_tests: str, covered_lines: set[int], baseline_s: float
    ) -> FeedbackMutationView:
        """Run every scoring mutant against the filtered suite of an attempt.

        Full results go to the sink; only the feedback view is returned.
        """

    def analyze_final(self, run_id: str, filtered_tests: str, covered_lines: set[int], baseline_s: float) -> None:
        """Run every mutant against the final suite; results go to the sink only."""

    def reuse_attempt_as_final(self, run_id: str, attempt_no: int) -> None:
        """Declare an attempt's results final because the suite did not change."""
