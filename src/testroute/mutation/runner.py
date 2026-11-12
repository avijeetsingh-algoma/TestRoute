"""Runs a filtered test suite against every scoring mutant.

Component: C9 (Mutation Analyzer), execution stage (every attempt).

Responsibility:
    Implements spec Section 6.6 and the MutationAnalyzer protocol used by the
    controller (C10):
    * mutants on lines the filtered suite never executed are
      SURVIVED_NOT_COVERED without running (covered-line pruning, FB4);
    * every other mutant runs in its own sandbox workspace with the mutated
      module and the filtered test file:
      ``python -m pytest -x -q -p no:cacheprovider`` without coverage;
    * workers run in parallel threads, each starting its own subprocess
      (threads, not fork-based multiprocessing, which Windows lacks); the
      default is ``max(1, os.cpu_count() // 2)``;
    * time limit per mutant = max(3 s, 5 x baseline), baseline being the
      filtered suite's run time on the original module;
    * exit code 1 = KILLED, 0 = SURVIVED; 2, 3, 4, 5 or a harness crash =
      ENV_ERROR, retried once, then kept as ENV_ERROR (excluded from the score,
      counted as a TOOL error);
    * a mutant that hits the limit is rerun once, alone (after the parallel
      batch, so no other mutant competes for the CPU), with double the limit;
      if it times out again it is TIMEOUT with ``timeout_diagnosis``
      ``likely_genuine`` when differential execution labelled it
      DIFF_TIMEOUT_ONLY, otherwise ``unexplained`` (FB3).

    Isolation (FB11): complete results go to the sink (C11); only the
    feedback-set view is returned to the controller.

Receives: a MutantSet, a MutationSink, the filtered tests, covered lines and
    baseline time.
Produces: FeedbackMutationView (to C10); MutantResult lists (to C11).
Talks to: ``sandbox`` (workspaces and process trees), ``results``, C11 sink.
Spec sections: 6.6.
Feedback IDs: FB3, FB4, FB11.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from testroute.mutation.mutant_set import MutantSet
from testroute.mutation.results import (
    ENV_ERROR,
    KILLED,
    SET_FEEDBACK,
    SURVIVED,
    SURVIVED_NOT_COVERED,
    TIMEOUT,
    FeedbackMutationView,
    MutantResult,
    MutationSink,
)
from testroute.sandbox import Workspace, build_sandbox_env, run_process
from testroute.test_runner import test_file_name

# Spec 6.6 time limit constants (also in config/experiment_main.yaml).
DEFAULT_MIN_LIMIT_S = 3.0
DEFAULT_MULTIPLIER = 5.0
DEFAULT_RERUN_MULTIPLIER = 2.0

LIKELY_GENUINE = "likely_genuine"
UNEXPLAINED = "unexplained"


def default_workers() -> int:
    """Spec 6.6 default: half the logical CPUs, at least one."""
    return max(1, (os.cpu_count() or 2) // 2)


class MutantRunner:
    """MutationAnalyzer that executes real mutants (one instance per run)."""

    def __init__(
        self,
        mutant_set: MutantSet,
        sink: MutationSink,
        *,
        workers: int | str | None = None,
        min_limit_s: float = DEFAULT_MIN_LIMIT_S,
        multiplier: float = DEFAULT_MULTIPLIER,
        rerun_multiplier: float = DEFAULT_RERUN_MULTIPLIER,
    ) -> None:
        """Args:
            mutant_set: Prepared mutants of the subject.
            sink: Receiver of complete results (the records store, C11).
            workers: Parallel workers; None or "auto" for the spec default.
            min_limit_s, multiplier: Time limit = max(min_limit_s,
                multiplier x baseline).
            rerun_multiplier: Limit multiplier for the single timeout rerun.
        """
        self.set = mutant_set
        self.sink = sink
        self.workers = default_workers() if workers in (None, "auto") else max(1, int(workers))
        self.min_limit_s = min_limit_s
        self.multiplier = multiplier
        self.rerun_multiplier = rerun_multiplier
        self.infos = mutant_set.infos()
        self.sources = mutant_set.sources()
        #: ENV_ERROR mutants per call, for the TOOL error tally (spec 6.6).
        self.env_errors: list[str] = []

    @classmethod
    def from_config(cls, mutant_set: MutantSet, sink: MutationSink, mutation_cfg: dict) -> "MutantRunner":
        """Build a runner from the ``mutation`` block of an experiment config."""
        return cls(
            mutant_set,
            sink,
            workers=mutation_cfg.get("workers", "auto"),
            min_limit_s=float(mutation_cfg.get("min_time_limit_s", DEFAULT_MIN_LIMIT_S)),
            multiplier=float(mutation_cfg.get("baseline_multiplier", DEFAULT_MULTIPLIER)),
            rerun_multiplier=float(mutation_cfg.get("timeout_rerun_multiplier", DEFAULT_RERUN_MULTIPLIER)),
        )

    # MutationAnalyzer protocol

    def analyze_attempt(
        self, run_id: str, attempt_no: int, filtered_tests: str, covered_lines: set[int], baseline_s: float
    ) -> FeedbackMutationView:
        """Run all scoring mutants; send all results to the sink; return the feedback view."""
        results = self.run_all(filtered_tests, covered_lines, baseline_s)
        self.sink.record_attempt_mutants(run_id, attempt_no, results, self.infos)
        return self.feedback_view(results)

    def analyze_final(self, run_id: str, filtered_tests: str, covered_lines: set[int], baseline_s: float) -> None:
        """Run all scoring mutants on the final suite; results go to the sink only."""
        self.sink.record_final_mutants(run_id, self.run_all(filtered_tests, covered_lines, baseline_s), self.infos)

    def reuse_attempt_as_final(self, run_id: str, attempt_no: int) -> None:
        """The final suite equals an attempt's suite; reuse its stored results."""
        self.sink.reuse_attempt_as_final(run_id, attempt_no)

    def feedback_view(self, results: list[MutantResult]) -> FeedbackMutationView:
        """Restrict results to the feedback set (the isolation boundary)."""
        feedback = tuple(r for r in results if r.set_label == SET_FEEDBACK)
        return FeedbackMutationView(
            results=feedback,
            mutants={k: v for k, v in self.infos.items() if v.set_label == SET_FEEDBACK},
            prompt_order=tuple(self.set.prompt_order),
        )

    # Execution

    def time_limit(self, baseline_s: float) -> float:
        """Spec 6.6: max(3 s, 5 x baseline)."""
        return max(self.min_limit_s, self.multiplier * baseline_s)

    def run_all(self, filtered_tests: str, covered_lines: set[int], baseline_s: float) -> list[MutantResult]:
        """Classify every scoring mutant against one filtered suite.

        Args:
            filtered_tests: Test file with only passing tests.
            covered_lines: Module lines the filtered suite executes.
            baseline_s: Filtered suite run time on the original module.

        Returns:
            One MutantResult per scoring mutant, in id order.

        Side effects:
            Starts one pytest subprocess per covered mutant (plus retries).
        """
        limit = self.time_limit(baseline_s)
        outcomes: dict[str, MutantResult] = {}
        to_run: list[str] = []
        for mutant_id, info in self.infos.items():
            if info.line not in covered_lines:
                outcomes[mutant_id] = MutantResult(mutant_id, info.set_label, SURVIVED_NOT_COVERED)
            else:
                to_run.append(mutant_id)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            first_pass = dict(zip(to_run, pool.map(lambda m: self._run_with_retry(m, filtered_tests, limit), to_run)))

        for mutant_id, (outcome, duration) in first_pass.items():
            info = self.infos[mutant_id]
            if outcome == TIMEOUT:
                # Rerun alone with double the limit, so a timeout caused by CPU
                # contention in the parallel batch is not mistaken for a hang.
                outcome, duration = self._run_with_retry(mutant_id, filtered_tests, limit * self.rerun_multiplier)
            diagnosis = ""
            if outcome == TIMEOUT:
                diagnosis = LIKELY_GENUINE if info.diff_timeout_only else UNEXPLAINED
            if outcome == ENV_ERROR:
                self.env_errors.append(mutant_id)
            outcomes[mutant_id] = MutantResult(mutant_id, info.set_label, outcome, round(duration, 4), diagnosis)
        return [outcomes[m] for m in sorted(outcomes)]

    def _run_with_retry(self, mutant_id: str, tests: str, limit: float) -> tuple[str, float]:
        """Run one mutant; an ENV_ERROR is retried once (spec 6.6)."""
        outcome, duration = self._run_once(mutant_id, tests, limit)
        if outcome == ENV_ERROR:
            outcome, duration = self._run_once(mutant_id, tests, limit)
        return outcome, duration

    def _run_once(self, mutant_id: str, tests: str, limit: float) -> tuple[str, float]:
        """Run the filtered suite against one mutant and classify the exit."""
        module = self.set.module_name
        start = time.perf_counter()
        try:
            with Workspace(prefix="testroute_mut_") as ws:
                ws.write(f"{module}.py", self.sources[mutant_id])
                ws.write(test_file_name(module), tests)
                ws.write("pytest.ini", "[pytest]\n")
                env = build_sandbox_env(ws.path, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
                proc = run_process(
                    [sys.executable, "-m", "pytest", test_file_name(module), "-x", "-q", "-p", "no:cacheprovider"],
                    ws.path, env, limit,
                )
        except OSError:
            # The harness itself failed (for example the interpreter could not
            # start): ENV_ERROR, not a judgement about the tests.
            return ENV_ERROR, time.perf_counter() - start
        if proc.timed_out:
            return TIMEOUT, proc.duration_s
        if proc.exit_code == 1:
            return KILLED, proc.duration_s
        if proc.exit_code == 0:
            return SURVIVED, proc.duration_s
        return ENV_ERROR, proc.duration_s
