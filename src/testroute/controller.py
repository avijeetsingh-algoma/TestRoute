"""Runs one (function, condition, trial): the generate, validate, feedback loop.

Component: C10 (Loop Controller). It also drives C3 to C9 for each call,
because the loop decides which of them run next.

Responsibility (D2 Section 6 steps 1 to 8, as fixed by the spec):
    1. Build the prompt (C3): T1 for call 1, T2 afterwards.
    2. Ask the router (C4) for the role, call the provider (C5).
    3. Extract (C6) and statically check (C7) the test file.
    4. Run it on the reference module with coverage (C8); build the filtered
       suite without failing tests.
    5. If at least one test passes, run mutation analysis (C9). Only the
       feedback-set view comes back here; held-out results go from C9
       straight to the records store (C11) (FB11).
    6. Keep the best suite: ranked by (passing tests > 0, strict feedback-set
       MS, branch coverage); a later attempt replaces it only when strictly
       better, so a later attempt can never make the result worse.
    7. Check the quality target on feedback-visible data only, for the
       feedback types of the condition (spec Section 9.3). Stop if met or if
       the call budget is used; otherwise build the feedback bundle for C3.
    8. Finalise: rerun the filtered best suite 3 times to detect flaky tests
       (V6), remove them, compute final coverage and mutation results, and
       write the run record (C11).

    Feedback bundle rules (filling rules in prompts/README.md):
    * CURRENT_TESTS is the raw file of the best attempt; if no attempt is
      usable yet, the latest attempt that produced code; if none produced
      code, the README placeholder.
    * EXEC, COV and MUT describe that same file, so the model is never told
      about failures of a file it is not shown.
    * NOCODE reports the previous reply when it had no usable test file:
      always when it yielded no code (NO_CODE, TRUNCATED), and also when its
      file failed a static check while a different, better file is shown.

    Failure classes (spec Section 8): INFRA errors from C5 end the run with
    status INFRA_ERROR (C1 re-queues it, never counted against the model);
    a persistent harness failure ends it with TOOL_ERROR; everything else is
    a MODEL outcome recorded per attempt.

Receives: a Subject (C2), a Condition (C4), a trial number, providers per
    role (C5), a MutationAnalyzer (C9), a ResultsStore (C11), the resolved
    experiment config.
Produces: records and artifacts through C11; returns the run record.
Talks to: C3, C4, C5, C6, C7, C8, C9 (feedback view only), C11, trace.
Spec sections: 5 (C10), 8, 9.2, 9.3, 10, 13.
Feedback IDs: FB3, FB4 (stage timings), FB7, FB11, FB12.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from testroute import cost
from testroute.llm.base import FINISH_LENGTH, InfraError, LLMProvider, LLMRequest, LLMResult, utc_now
from testroute.logging_setup import RunLog
from testroute.mutation.results import FeedbackMutationView, MutationAnalyzer
from testroute.parser import ERROR_NO_CODE, parse_response
from testroute.prompts import COV, EXEC, MUT, FailureLine, FeedbackBundle, Prompt, PromptBuilder
from testroute.records import STATUS_COMPLETE, STATUS_INFRA_ERROR, STATUS_TOOL_ERROR, ResultsStore, config_hash, git_commit
from testroute.router import CLOUD, LOCAL, Condition, Router
from testroute.static_check import StaticResult, check_test_file
from testroute.subjects import Subject
from testroute.test_runner import (
    OUTCOME_PASSED,
    OUTCOME_TIMEOUT,
    STATUS_COLLECTION_ERROR,
    STATUS_OK,
    STATUS_TIMEOUT,
    STATUS_TOOL_ERROR as SUITE_TOOL_ERROR,
    CoverageResult,
    SuiteRunResult,
    TestCaseResult,
    filter_failing_tests,
    run_suite,
)

# Error types (spec Section 8) produced here rather than by C6 or C7.
TRUNCATED = "TRUNCATED"
COLLECTION_ERROR = "COLLECTION_ERROR"
TEST_FAIL = "TEST_FAIL"
TIMEOUT = "TIMEOUT"
FLAKY = "FLAKY"
INFRA_ERROR = "INFRA_ERROR"
TOOL_ERROR = "TOOL_ERROR"

# Test name used in the EXEC block for whole-file failures (prompts/README.md).
WHOLE_FILE = "(whole file)"

# Static error types that mean the file never ran (V2, V3).
STATIC_TYPES = {"SYNTAX_ERROR", "WRONG_IMPORT", "DISALLOWED_IMPORT", "NO_TESTS", "NO_ASSERT", "UNSAFE"}

# A filtered suite can still fail if a kept test depended on a removed one
# (shared module state). Filtering is repeated at most this many times.
MAX_FILTER_PASSES = 3


class ToolError(Exception):
    """A harness or external tool failure that persisted after one retry."""


@dataclass
class Attempt:
    """Everything measured for one call's test file.

    Attributes:
        attempt_no: Equal to the call number.
        role: ``local`` or ``cloud``.
        raw_tests: Extracted file (None when V1 failed).
        filtered_tests: Raw file without non-passing tests (None if unusable).
        error_type: First failed check (spec Section 8), None if all passed.
        error_message: Message of that failure.
        validation: V1 to V5 -> pass, fail or not_run.
        static: C7 result.
        run: C8 result of the raw file.
        coverage: Coverage of the filtered suite.
        mutation: Feedback-set view from C9 (never held-out data).
        removed_tests: Tests removed to build the filtered suite.
        baseline_s: Run time of the filtered suite (mutant time limits, 6.6).
        timings: Seconds per stage: llm, parse, static, tests, coverage, mutation.
    """

    attempt_no: int
    role: str
    raw_tests: str | None = None
    filtered_tests: str | None = None
    error_type: str | None = None
    error_message: str = ""
    validation: dict[str, str] = field(default_factory=lambda: {f"V{i}": "not_run" for i in range(1, 6)})
    static: StaticResult | None = None
    run: SuiteRunResult | None = None
    coverage: CoverageResult | None = None
    mutation: FeedbackMutationView | None = None
    removed_tests: list[str] = field(default_factory=list)
    baseline_s: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def passing(self) -> int:
        """Number of passing test cases in the raw run."""
        return len(self.run.passed) if self.run else 0

    @property
    def usable(self) -> bool:
        """At least one test passes (the suite can be measured)."""
        return self.passing > 0 and self.filtered_tests is not None

    @property
    def feedback_ms(self) -> float | None:
        """Strict feedback-set MS, or None without mutation results."""
        return self.mutation.strict_ms if self.mutation else None

    def rank_key(self) -> tuple[int, float, float]:
        """Best-suite ranking key (D2 Section 6 step 6)."""
        ms = self.feedback_ms if self.feedback_ms is not None else -1.0
        branch = self.coverage.branch_percent if self.coverage else -1.0
        return (1 if self.usable else 0, ms, branch)


@dataclass
class Decision:
    """Outcome of the target check after an attempt (trace file 10)."""

    best_attempt: int | None
    target_met: bool
    unmet: list[str]
    next_action: str


class RunController:
    """Executes one run end to end. Create one instance per run."""

    def __init__(
        self,
        *,
        subject: Subject,
        condition: Condition,
        trial: int,
        providers: dict[str, LLMProvider],
        analyzer: MutationAnalyzer,
        store: ResultsStore,
        config: dict[str, Any],
        pricing: dict[str, Any] | None = None,
        prompt_builder: PromptBuilder | None = None,
        echo: bool = False,
    ) -> None:
        """Set up a run.

        Args:
            subject: Validated function under test (C2).
            condition: Condition of this run (C4).
            trial: Trial number (1-based); local seeds are 1000 x trial + call number.
            providers: Provider per role; only roles the condition uses are
                needed. A missing role is an INFRA failure, never a silent
                fallback to the other model.
            analyzer: Mutation analyzer (C9) wired to ``store`` as its sink.
            store: Results store (C11).
            config: Resolved experiment config (quality_target, test_runner,
                mutation blocks).
            pricing: Pricing config; loaded from config/pricing.yaml if None.
            prompt_builder: Template filler; loaded from prompts/ if None.
            echo: Also print run.log lines to the console.
        """
        self.subject = subject
        self.condition = condition
        self.trial = trial
        self.providers = providers
        self.analyzer = analyzer
        self.store = store
        self.config = config
        self.pricing = pricing if pricing is not None else cost.load_pricing()
        self.prompts = prompt_builder or PromptBuilder()
        self.router = Router(condition)
        self.run_id = f"{subject.function_id}__{condition.name}__t{trial}"
        self.trace = store.trace(self.run_id)
        self.log = RunLog(store.run_dir(self.run_id) / "run.log", echo=echo)
        runner_cfg = config.get("test_runner", {})
        self.per_test_timeout = float(runner_cfg.get("per_test_timeout_s", 5))
        self.suite_timeout = float(runner_cfg.get("per_suite_timeout_s", 60))
        self.stability_reruns = int(runner_cfg.get("stability_reruns", 3))
        self.target = config.get("quality_target", {})
        self.max_mutants = int(config.get("mutation", {}).get("max_mutants_in_prompt", 5))

    # Main loop

    def execute(self) -> dict[str, Any]:
        """Run the loop and write all records.

        Returns:
            The completed run record (as written to runs.jsonl).

        Raises:
            Nothing for INFRA or TOOL failures: they are recorded in the run
            status. Programming errors propagate.

        Side effects:
            Calls LLM providers; starts sandboxed subprocesses; writes records,
            artifacts and trace files.
        """
        started_utc, t0 = utc_now(), time.perf_counter()
        self.log.log("C1", f"run {self.run_id} started: condition {self.condition.name}, trial {self.trial}")
        self.trace.text("00_input_function.py", self.subject.source)
        self.trace.json("01_C2_subject.json", self.subject.to_dict())
        self.log.log("C2", f"subject {self.subject.module_name}.{self.subject.entry_point} loaded "
                     f"({len(self.subject.source.splitlines())} lines, import {self.subject.import_seconds:.3f} s)")

        calls: list[dict[str, Any]] = []
        attempts: list[Attempt] = []
        best: Attempt | None = None
        bundle: FeedbackBundle | None = None
        decision: Decision | None = None
        escalated, escalation_reason = False, ""
        status, error = STATUS_COMPLETE, ""
        final: dict[str, Any] = {"usable": False}

        try:
            for call_no in range(1, self.condition.max_calls + 1):
                role = self.router.role_for_call(call_no)
                if self.router.is_escalation(call_no):
                    escalated = True
                    unmet = ", ".join(decision.unmet) if decision else "no attempt"
                    escalation_reason = f"target not met after {self.condition.local_calls} local calls ({unmet})"
                    self.log.log("C4", f"escalating to the cloud model: {escalation_reason}")
                prompt = self._build_prompt(call_no, attempts, best, bundle)
                self.log.log("C4", f"call {call_no} routed to {role}")
                result, call_record = self._call(call_no, role, prompt, calls)
                attempt = self._evaluate(call_no, role, result)
                attempts.append(attempt)
                call_record.update(
                    error_class="MODEL" if attempt.error_type else "",
                    error_type=attempt.error_type or "",
                )
                self.store.write_call(call_record)

                if attempt.usable and (best is None or attempt.rank_key() > best.rank_key()):
                    best = attempt
                decision = self._decide(call_no, best)
                self.trace.json("10_C10_decision.json", asdict(decision), call_no)
                self.log.log(
                    "C10",
                    f"best suite: attempt {decision.best_attempt}; target "
                    f"{'met' if decision.target_met else 'not met (' + ', '.join(decision.unmet) + ')'}; "
                    f"next: {decision.next_action}",
                )
                bundle = None
                if decision.next_action == "feedback":
                    bundle = self._bundle(attempts, best)
                    path = self.store.write_artifact(self.run_id, f"attempt{call_no}_feedback.json", _json(bundle.to_dict()))
                    self.trace.json("11_C10_to_C3_feedback_bundle.json", bundle.to_dict(), call_no)
                    self._write_attempt(attempt, self.store.relative(path))
                else:
                    self._write_attempt(attempt, "")
                    break
            final = self._finalize(best)
        except InfraError as exc:
            status, error = STATUS_INFRA_ERROR, str(exc)
            self.log.log("C5", f"INFRA failure, run will be re-queued: {exc}")
        except ToolError as exc:
            status, error = STATUS_TOOL_ERROR, str(exc)
            self.log.log("C8", f"TOOL failure, run will be repeated: {exc}")

        record = self._run_record(
            started_utc, time.perf_counter() - t0, status, error, calls, attempts, best, decision,
            escalated, escalation_reason, final,
        )
        record = self.store.write_run(record, usable=bool(final.get("usable")) and status == STATUS_COMPLETE)
        self.log.log("C11", f"run record written: status {status}, held-out strict MS {record.get('heldout_strict_ms')}")
        return record

    # Steps 1 and 2: prompt and call

    def _build_prompt(
        self, call_no: int, attempts: list[Attempt], best: Attempt | None, bundle: FeedbackBundle | None
    ) -> Prompt:
        """Fill T1 for call 1 and T2 for later calls (C3)."""
        if call_no == 1:
            prompt = self.prompts.generation_prompt(self.subject)
        else:
            current = self._current_attempt(attempts, best)
            prompt = self.prompts.feedback_prompt(
                self.subject,
                current.raw_tests if current else None,
                bundle or FeedbackBundle(),
                self.condition.feedback_types,
                self.max_mutants,
            )
        self.log.log(
            "C3",
            f"call {call_no}: template {prompt.template_id}, about {prompt.estimated_tokens} tokens"
            + (f", blocks {'+'.join(prompt.blocks)}" if prompt.blocks else "")
            + (f", shortened: {'; '.join(prompt.shortening)}" if prompt.shortening else "")
            + (" (still over budget)" if prompt.over_budget else ""),
        )
        return prompt

    def _call(
        self, call_no: int, role: str, prompt: Prompt, calls: list[dict[str, Any]]
    ) -> tuple[LLMResult, dict[str, Any]]:
        """Send one request through the provider for ``role`` (C5).

        Returns:
            (result, call record without the error fields, which the caller
            fills after evaluating the attempt).

        Raises:
            InfraError: when the provider fails or no provider serves ``role``;
                the failed call is recorded first.
        """
        request_info = dict(prompt.request_dict(), role=role, call_no=call_no)
        self.trace.json("02_C3_to_C4_prompt_request.json", request_info, call_no)
        prompt_text = f"SYSTEM:\n{prompt.system}\n\nUSER:\n{prompt.user}\n"
        self.trace.text("03_prompt_sent.txt", prompt_text, call_no)
        prompt_path = self.store.write_artifact(self.run_id, f"call{call_no}_prompt.txt", prompt_text)
        base = {
            "run_id": self.run_id,
            "call_no": call_no,
            "role": role,
            "function_id": self.subject.function_id,
            "condition": self.condition.name,
            "trial": self.trial,
            "template_id": prompt.template_id,
            "template_hash": prompt.template_hash,
            "prompt_sha256": prompt.sha256,
            "prompt_path": self.store.relative(prompt_path),
            "estimated_tokens": prompt.estimated_tokens,
            "shortening": list(prompt.shortening),
            "over_budget": prompt.over_budget,
        }
        provider = self.providers.get(role)
        try:
            if provider is None:
                raise InfraError(f"no provider configured for role '{role}'")
            request = LLMRequest(
                run_id=self.run_id, call_no=call_no, role=role, system=prompt.system, user=prompt.user,
                prompt_sha256=prompt.sha256, function_id=self.subject.function_id,
                condition=self.condition.name, trial=self.trial,
            )
            result = provider.complete(request)
        except InfraError as exc:
            record = dict(base, provider=provider.name if provider else "", error_class="INFRA",
                          error_type=INFRA_ERROR, error_message=str(exc), retries=exc.retries,
                          started_utc=utc_now())
            self.store.write_call(record)
            calls.append(record)
            raise
        response_path = self.store.write_artifact(self.run_id, f"call{call_no}_response.txt", result.text)
        self.trace.text("04_C5_raw_response.txt", result.text, call_no)
        self.trace.json("05_C5_to_C6_llm_result.json", result.to_dict(), call_no)
        record = dict(
            base,
            provider=result.provider,
            model=result.model,
            fingerprint=result.fingerprint,
            response_path=self.store.relative(response_path),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_hit_tokens=result.cache_hit_tokens,
            cache_miss_tokens=result.cache_miss_tokens,
            reasoning_tokens=result.reasoning_tokens,
            latency_s=round(result.latency_s, 4),
            local_eval_s=round(result.local_eval_s, 4),
            finish_reason=result.finish_reason,
            retries=result.retries,
            seed=result.seed,
            peak=cost.is_peak(result.started_utc, self.pricing) if role == CLOUD else None,
            started_utc=result.started_utc,
            ended_utc=result.ended_utc,
        )
        calls.append(record)
        tag = "[REPLAY] " if result.provider == "replay" else ""
        self.log.log(
            "C5",
            f"{tag}call {call_no}: {result.provider} {result.model}, {result.input_tokens} in / "
            f"{result.output_tokens} out tokens, {result.latency_s:.2f} s, finish {result.finish_reason}",
        )
        return result, record

    # Steps 3 to 5: evaluate one response

    def _evaluate(self, attempt_no: int, role: str, result: LLMResult) -> Attempt:
        """Run C6, C7, C8 and C9 on one response and return the attempt."""
        attempt = Attempt(attempt_no=attempt_no, role=role)
        attempt.timings["llm"] = round(result.latency_s, 4)
        truncated = result.finish_reason == FINISH_LENGTH

        start = time.perf_counter()
        parsed = parse_response(result.text)
        attempt.timings["parse"] = round(time.perf_counter() - start, 4)
        self.trace.json("06_C6_parse_report.json", parsed.report, attempt_no)
        if parsed.code is None:
            attempt.validation["V1"] = "fail"
            attempt.error_type = TRUNCATED if truncated else ERROR_NO_CODE
            attempt.error_message = parsed.report.get("rule", "")
            self.log.log("C6", f"no usable code ({attempt.error_type}): {attempt.error_message}")
            return attempt
        attempt.validation["V1"] = "pass"
        attempt.raw_tests = parsed.code
        self.trace.text("06_C6_parsed_tests.py", parsed.code, attempt_no)
        self.store.write_artifact(self.run_id, f"attempt{attempt_no}_tests_raw.py", parsed.code)
        self.log.log("C6", f"extracted {len(parsed.code.splitlines())} lines ({parsed.report.get('rule')})")

        start = time.perf_counter()
        static = check_test_file(parsed.code, self.subject.module_name, self.subject.entry_point)
        attempt.timings["static"] = round(time.perf_counter() - start, 4)
        attempt.static = static
        self.trace.json("07_C7_static_validation.json", static.to_dict(), attempt_no)
        if not static.ok:
            syntax = static.error_type == "SYNTAX_ERROR"
            attempt.validation["V2"] = "fail" if syntax else "pass"
            if not syntax:
                attempt.validation["V3"] = "fail"
            # A cut-off reply normally fails to parse; the cause is recorded
            # as TRUNCATED, not SYNTAX_ERROR (spec Section 8).
            attempt.error_type = TRUNCATED if truncated and syntax else static.error_type
            attempt.error_message = static.message
            self.log.log("C7", f"static check failed: {attempt.error_type}: {static.message}")
            return attempt
        attempt.validation["V2"] = attempt.validation["V3"] = "pass"
        self.log.log("C7", f"static check passed ({len(static.test_names)} tests)")

        start = time.perf_counter()
        run = self._run_tests(parsed.code)
        attempt.timings["tests"] = round(time.perf_counter() - start, 4)
        attempt.run = run
        if run.status == STATUS_COLLECTION_ERROR:
            attempt.validation["V4"] = "fail"
            attempt.error_type, attempt.error_message = COLLECTION_ERROR, run.message
            self._trace_tests(attempt)
            self.log.log("C8", f"collection error: {run.message.splitlines()[0] if run.message else ''}")
            return attempt
        attempt.validation["V4"] = "pass"
        if run.status == STATUS_TIMEOUT:
            attempt.validation["V5"] = "fail"
            attempt.error_type, attempt.error_message = TIMEOUT, run.message
            self._trace_tests(attempt)
            self.log.log("C8", f"suite timeout: {run.message}")
            return attempt

        failing = run.not_passed
        attempt.validation["V5"] = "fail" if failing else "pass"
        if failing:
            only_timeouts = all(t.outcome == OUTCOME_TIMEOUT for t in failing)
            attempt.error_type = TIMEOUT if only_timeouts and not run.passed else TEST_FAIL
            attempt.error_message = f"{len(failing)} of {len(run.tests)} test cases did not pass"

        if run.passed:
            start = time.perf_counter()
            self._build_filtered_suite(attempt, parsed.code, run)
            attempt.timings["coverage"] = round(time.perf_counter() - start, 4)
        self._trace_tests(attempt)
        cov = attempt.coverage
        self.log.log(
            "C8",
            f"tests: {len(run.passed)} passed, {len(failing)} not passed"
            + (f"; filtered suite line {cov.line_percent:.1f}% branch {cov.branch_percent:.1f}%" if cov else ""),
        )

        if attempt.usable and cov is not None:
            start = time.perf_counter()
            attempt.mutation = self.analyzer.analyze_attempt(
                self.run_id, attempt_no, attempt.filtered_tests or "", set(cov.executed_lines), attempt.baseline_s
            )
            attempt.timings["mutation"] = round(time.perf_counter() - start, 4)
            self.trace.json("09_C9_mutation_feedback_view.json", attempt.mutation.to_dict(), attempt_no)
            self.log.log(
                "C9",
                f"feedback-set strict MS {_fmt(attempt.mutation.strict_ms)} over {len(attempt.mutation.results)} mutants",
            )
        return attempt

    def _run_tests(self, test_source: str, with_coverage: bool = True) -> SuiteRunResult:
        """Run a suite on the reference module; retry a TOOL_ERROR once.

        Raises:
            ToolError: when the harness fails twice in a row (spec C8 / D2
                Table 4: record, retry once, then repeat the run after fixing).
        """
        for _ in range(2):
            run = run_suite(
                self.subject.module_name, self.subject.source, test_source,
                per_test_timeout_s=self.per_test_timeout, suite_timeout_s=self.suite_timeout,
                with_coverage=with_coverage,
            )
            if run.status != SUITE_TOOL_ERROR:
                return run
            self.log.log("C8", f"tool error, retrying once: {run.message.splitlines()[0] if run.message else ''}")
        raise ToolError(run.message)

    def _build_filtered_suite(self, attempt: Attempt, raw: str, run: SuiteRunResult) -> None:
        """Remove non-passing tests and measure the filtered suite's coverage."""
        if not run.not_passed:
            attempt.filtered_tests, attempt.coverage, attempt.baseline_s = raw, run.coverage, run.duration_s
            self.store.write_artifact(self.run_id, f"attempt{attempt.attempt_no}_tests_filtered.py", raw)
            return
        source, results = raw, list(run.tests)
        for _ in range(MAX_FILTER_PASSES):
            source, removed = filter_failing_tests(source, results)
            attempt.removed_tests += removed
            filtered_run = self._run_tests(source)
            if filtered_run.status == STATUS_OK and filtered_run.passed and not filtered_run.not_passed:
                attempt.filtered_tests = source
                attempt.coverage = filtered_run.coverage
                attempt.baseline_s = filtered_run.duration_s
                self.store.write_artifact(self.run_id, f"attempt{attempt.attempt_no}_tests_filtered.py", source)
                return
            if filtered_run.status != STATUS_OK or not filtered_run.passed:
                break
            results = filtered_run.tests
        # The filtered suite could not be made to pass on its own: treat the
        # attempt as unusable rather than score an unstable suite.
        attempt.filtered_tests = None

    def _trace_tests(self, attempt: Attempt) -> None:
        """Write trace files 08 for an attempt."""
        data = attempt.run.to_dict() if attempt.run else {}
        data.pop("coverage", None)
        data["removed_from_filtered_suite"] = attempt.removed_tests
        data["filtered_suite_usable"] = attempt.usable
        self.trace.json("08_C8_test_results.json", data, attempt.attempt_no)
        self.trace.json("08_C8_coverage.json", asdict(attempt.coverage) if attempt.coverage else {}, attempt.attempt_no)

    # Steps 6 and 7: decide and build feedback

    def _decide(self, call_no: int, best: Attempt | None) -> Decision:
        """Check the quality target on the best suite (feedback-visible data only)."""
        unmet: list[str] = []
        types = self.condition.feedback_types
        if best is None:
            unmet.append("no usable suite")
        else:
            if EXEC in types and self.target.get("all_tests_pass", True) and best.run and best.run.not_passed:
                unmet.append("not all tests pass")
            if COV in types and best.coverage is not None:
                if best.coverage.line_percent < float(self.target.get("min_line_coverage", 90)):
                    unmet.append(f"line coverage {best.coverage.line_percent:.1f} < {self.target.get('min_line_coverage', 90)}")
                if best.coverage.branch_percent < float(self.target.get("min_branch_coverage", 90)):
                    unmet.append(f"branch coverage {best.coverage.branch_percent:.1f} < {self.target.get('min_branch_coverage', 90)}")
            # A function without feedback mutants has nothing to improve, so
            # an undefined score does not block the target.
            ms = best.feedback_ms
            if MUT in types and ms is not None and ms < float(self.target.get("min_feedback_strict_ms", 80)):
                unmet.append(f"feedback strict MS {ms:.1f} < {self.target.get('min_feedback_strict_ms', 80)}")
        met = not unmet
        if met:
            action = "stop: target met"
        elif call_no >= self.condition.max_calls:
            action = "stop: call budget used"
        else:
            action = "feedback"
        return Decision(best.attempt_no if best else None, met, unmet, action)

    @staticmethod
    def _current_attempt(attempts: list[Attempt], best: Attempt | None) -> Attempt | None:
        """The attempt whose test file is shown as CURRENT_TESTS."""
        if best is not None:
            return best
        with_code = [a for a in attempts if a.raw_tests is not None]
        return with_code[-1] if with_code else None

    def _bundle(self, attempts: list[Attempt], best: Attempt | None) -> FeedbackBundle:
        """Build the feedback bundle for the next T2 prompt (see module docstring)."""
        latest = attempts[-1]
        current = self._current_attempt(attempts, best)
        nocode = None
        if latest.raw_tests is None:
            nocode = latest.error_type
        elif current is not latest and latest.error_type in STATIC_TYPES | {TRUNCATED}:
            nocode = latest.error_type
        if current is None:
            return FeedbackBundle(nocode_reason=nocode)

        failures: list[FailureLine] = []
        if current.error_type in STATIC_TYPES | {TRUNCATED, COLLECTION_ERROR} and not current.usable:
            failures.append(FailureLine(WHOLE_FILE, current.error_type or "", _first_line(current.error_message)))
        elif current.run is not None and current.run.status == STATUS_TIMEOUT:
            failures.append(FailureLine(WHOLE_FILE, TIMEOUT, current.run.message))
        elif current.run is not None:
            failures += [FailureLine(_display_name(t), t.error_type, t.message) for t in current.run.not_passed]

        uncovered: tuple[tuple[int, str], ...] = ()
        branches: tuple[tuple[int, int], ...] = ()
        if current.coverage is not None:
            lines = self.subject.source.splitlines()
            uncovered = tuple((n, lines[n - 1] if 0 < n <= len(lines) else "") for n in current.coverage.missing_lines)
            branches = tuple(current.coverage.missing_branches)
        survivors = tuple(current.mutation.survivors_for_prompt(self.max_mutants)) if current.mutation else ()
        return FeedbackBundle(nocode, tuple(failures), uncovered, branches, survivors)

    def _write_attempt(self, attempt: Attempt, feedback_path: str) -> None:
        """Write the attempts.jsonl record (no held-out data; C11 adds counts)."""
        run = attempt.run
        self.store.write_attempt(
            {
                "run_id": self.run_id,
                "attempt_no": attempt.attempt_no,
                "role": attempt.role,
                "validation": attempt.validation,
                "error_type": attempt.error_type or "",
                "error_message": attempt.error_message,
                "tests": [asdict(t) for t in run.tests] if run else [],
                "removed_tests": attempt.removed_tests,
                "usable": attempt.usable,
                "line_coverage": attempt.coverage.line_percent if attempt.coverage else None,
                "branch_coverage": attempt.coverage.branch_percent if attempt.coverage else None,
                "uncovered_lines": attempt.coverage.missing_lines if attempt.coverage else [],
                "uncovered_branches": attempt.coverage.missing_branches if attempt.coverage else [],
                "feedback_strict_ms": attempt.feedback_ms,
                "feedback_path": feedback_path,
                "timings": attempt.timings,
            }
        )

    # Step 8: finalise

    def _finalize(self, best: Attempt | None) -> dict[str, Any]:
        """Stability reruns (V6), flaky removal, final coverage and mutation.

        Returns:
            Final suite facts for the run record.
        """
        if best is None or best.filtered_tests is None:
            self.store.write_artifact(self.run_id, "final_tests.py", "# no usable test suite was produced\n")
            self.log.log("C10", "finalise: no usable suite; coverage and mutation scores are 0")
            return {"usable": False, "tests_valid": 0, "tests_flaky": 0, "stability_s": 0.0, "final_mutation_s": 0.0}

        suite = best.filtered_tests
        flaky: dict[tuple[str | None, str], TestCaseResult] = {}
        start = time.perf_counter()
        for rerun in range(1, self.stability_reruns + 1):
            result = self._run_tests(suite, with_coverage=False)
            if result.status != STATUS_OK:
                # The whole suite misbehaved on a rerun (timeout or collection
                # error): every test is unstable.
                flaky.update({(t.class_name, t.function): t for t in best.run.passed} if best.run else {})
                self.log.log("C10", f"stability rerun {rerun}: suite status {result.status}; all tests unstable")
                break
            for case in result.not_passed:
                flaky[(case.class_name, case.function)] = case
        stability_s = time.perf_counter() - start

        coverage = best.coverage
        mutation_s = 0.0
        if flaky:
            suite, removed = filter_failing_tests(suite, list(flaky.values()))
            self.log.log("C10", f"finalise: removed {len(removed)} flaky tests ({', '.join(removed)})")
            remaining = check_test_file(suite, self.subject.module_name, self.subject.entry_point)
            if not remaining.test_names:
                self.store.write_artifact(self.run_id, "final_tests.py", suite)
                return {"usable": False, "tests_valid": 0, "tests_flaky": len(flaky), "stability_s": stability_s,
                        "final_mutation_s": 0.0}
            final_run = self._run_tests(suite)
            if final_run.status != STATUS_OK or not final_run.passed or final_run.not_passed:
                self.store.write_artifact(self.run_id, "final_tests.py", suite)
                return {"usable": False, "tests_valid": 0, "tests_flaky": len(flaky), "stability_s": stability_s,
                        "final_mutation_s": 0.0}
            coverage = final_run.coverage
            start = time.perf_counter()
            self.analyzer.analyze_final(self.run_id, suite, set(coverage.executed_lines) if coverage else set(),
                                        final_run.duration_s)
            mutation_s = time.perf_counter() - start
        else:
            # Suite unchanged: the best attempt's mutation results are final.
            self.analyzer.reuse_attempt_as_final(self.run_id, best.attempt_no)

        self.store.write_artifact(self.run_id, "final_tests.py", suite)
        self.trace.text("20_final_tests.py", suite)
        valid = len(check_test_file(suite, self.subject.module_name, self.subject.entry_point).test_names)
        self.log.log(
            "C10",
            f"finalise: {valid} tests kept, {len(flaky)} flaky; line {coverage.line_percent if coverage else 0:.1f}% "
            f"branch {coverage.branch_percent if coverage else 0:.1f}%",
        )
        return {
            "usable": True,
            "tests_valid": valid,
            "tests_flaky": len(flaky),
            "line_coverage": coverage.line_percent if coverage else 0.0,
            "branch_coverage": coverage.branch_percent if coverage else 0.0,
            "stability_s": stability_s,
            "final_mutation_s": mutation_s,
        }

    def _run_record(
        self,
        started_utc: str,
        total_s: float,
        status: str,
        error: str,
        calls: list[dict[str, Any]],
        attempts: list[Attempt],
        best: Attempt | None,
        decision: Decision | None,
        escalated: bool,
        escalation_reason: str,
        final: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble the runs.jsonl record (C11 adds the mutation scores)."""
        ok_calls = [c for c in calls if c.get("error_class") != "INFRA"]
        by_role = {role: [c for c in ok_calls if c["role"] == role] for role in (LOCAL, CLOUD)}
        list_cost = tod_cost = 0.0
        for call in by_role[CLOUD]:
            a, b = cost.cloud_call_costs(call, self.pricing)
            list_cost, tod_cost = list_cost + a, tod_cost + b
        local_s = sum(float(c.get("local_eval_s") or 0) for c in by_role[LOCAL])
        kwh, energy_usd = cost.energy_estimate(local_s, self.pricing)
        selected = best or (attempts[-1] if attempts else None)
        generated = len(selected.static.test_names) if selected and selected.static else 0
        passing_funcs = _passing_functions(selected)
        usable = bool(final.get("usable")) and status == STATUS_COMPLETE

        def _sum(stage: str) -> float:
            return round(sum(a.timings.get(stage, 0.0) for a in attempts), 4)

        return {
            "run_id": self.run_id,
            "function_id": self.subject.function_id,
            "condition": self.condition.name,
            "policy": self.condition.policy,
            "feedback_mode": self.condition.feedback_mode,
            "trial": self.trial,
            "seeds": [c.get("seed") for c in ok_calls],
            "status": status,
            "error": error,
            "started_utc": started_utc,
            "ended_utc": utc_now(),
            "git_commit": git_commit(),
            "config_hash": config_hash(self.config),
            "template_hash": self.prompts.template_hash,
            "providers": sorted({c.get("provider", "") for c in calls if c.get("provider")}),
            "replay": any(c.get("provider") == "replay" for c in calls),
            "models": {role: sorted({c.get("model", "") for c in by_role[role]}) for role in (LOCAL, CLOUD)},
            "fingerprints": sorted({c.get("fingerprint", "") for c in ok_calls if c.get("fingerprint")}),
            "calls_total": len(ok_calls),
            "calls_local": len(by_role[LOCAL]),
            "calls_cloud": len(by_role[CLOUD]),
            "tokens": {
                role: {
                    "input": sum(int(c.get("input_tokens") or 0) for c in by_role[role]),
                    "output": sum(int(c.get("output_tokens") or 0) for c in by_role[role]),
                    "cache_hit": sum(int(c.get("cache_hit_tokens") or 0) for c in by_role[role]),
                    "cache_miss": sum(int(c.get("cache_miss_tokens") or 0) for c in by_role[role]),
                }
                for role in (LOCAL, CLOUD)
            },
            "escalated": escalated,
            "escalation_reason": escalation_reason,
            "attempts": len(attempts),
            "attempt_error_types": [a.error_type or "" for a in attempts],
            "best_attempt": best.attempt_no if best else None,
            "target_met": bool(decision and decision.target_met),
            "usable": usable,
            "tests_generated": generated,
            "tests_valid": final.get("tests_valid", 0) if usable else 0,
            "tests_failing": len(selected.removed_tests) if selected else 0,
            "tests_flaky": final.get("tests_flaky", 0),
            "test_pass_rate": round(100.0 * passing_funcs / generated, 2) if generated else 0.0,
            "line_coverage": final.get("line_coverage", 0.0) if usable else 0.0,
            "branch_coverage": final.get("branch_coverage", 0.0) if usable else 0.0,
            "api_cost_list_usd": round(list_cost, 6),
            "api_cost_time_of_day_usd": round(tod_cost, 6),
            "local_compute_s": round(local_s, 3),
            "energy_kwh_rough": round(kwh, 6),
            "energy_cost_usd_rough": round(energy_usd, 6),
            "llm_time_s": round(sum(float(c.get("latency_s") or 0) for c in ok_calls), 4),
            "parse_time_s": _sum("parse"),
            "static_time_s": _sum("static"),
            "test_time_s": round(_sum("tests") + _sum("coverage") + float(final.get("stability_s", 0.0)), 4),
            "mutation_time_s": round(_sum("mutation") + float(final.get("final_mutation_s", 0.0)), 4),
            "total_time_s": round(total_s, 4),
            "final_tests_path": f"artifacts/{self.run_id}/final_tests.py",
        }


def _passing_functions(attempt: Attempt | None) -> int:
    """Test functions whose every case passed (for the test pass rate)."""
    if attempt is None or attempt.run is None:
        return 0
    status: dict[tuple[str | None, str], bool] = {}
    for case in attempt.run.tests:
        key = (case.class_name, case.function)
        status[key] = status.get(key, True) and case.outcome == OUTCOME_PASSED
    return sum(1 for ok in status.values() if ok)


def _display_name(case: TestCaseResult) -> str:
    """Test name for the EXEC block: ``test_x[1]`` or ``TestC::test_x``."""
    return case.nodeid.split("::", 1)[-1]


def _first_line(text: str) -> str:
    """First line of a possibly multi-line message."""
    return text.strip().splitlines()[0] if text.strip() else ""


def _fmt(value: float | None) -> str:
    """Format a score that may be undefined."""
    return "n/a" if value is None else f"{value:.1f}%"


def _json(data: Any) -> str:
    """Indented JSON text for artifacts."""
    return json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
