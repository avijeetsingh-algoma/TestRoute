"""Integration tests: full runs on a tiny bundled function with the fake provider.

Milestone 2 acceptance (spec Sections 14 and 15): a two-attempt feedback loop
and an ESC escalation run end to end through C2 to C11 and the trace export,
offline, and every record and trace file exists. Mutation outcomes come from
the scripted analyzer in fakes.py until the real analyzer lands (milestone 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakes import DATA_DIR, HELDOUT_MARKER, NEGATIVE_ONLY_TESTS, STRONG_TESTS, WEAK_TESTS, ScriptedAnalyzer
from testroute.controller import RunController
from testroute.llm.fake import FakeProvider, canned
from testroute.records import ResultsStore
from testroute.router import parse_condition
from testroute.settings import load_config
from testroute.subjects import load_subject
from testroute.trace import export_trace, read_jsonl


@pytest.fixture(scope="module")
def subject():
    """The bundled classify function, validated by C2 (including the import check)."""
    return load_subject(DATA_DIR / "classify.py", "classify", function_id="tiny_classify")


def _run(tmp_path: Path, subject, condition: str, local: list | None = None, cloud: list | None = None):
    """Execute one run and return (record, store, providers)."""
    store = ResultsStore(tmp_path / "results")
    providers = {}
    if local is not None:
        providers["local"] = FakeProvider(local, model="fake-local")
    if cloud is not None:
        providers["cloud"] = FakeProvider(cloud, model="fake-cloud")
    controller = RunController(
        subject=subject,
        condition=parse_condition(condition),
        trial=1,
        providers=providers,
        analyzer=ScriptedAnalyzer(store),
        store=store,
        config=load_config("experiment_main.yaml"),
    )
    return controller.execute(), store, providers


def test_two_attempt_feedback_loop(tmp_path: Path, subject) -> None:
    """C-F4: a weak first suite gets EXEC, COV and MUT feedback, the second suite meets the target."""
    record, store, providers = _run(tmp_path, subject, "C-F4", cloud=[WEAK_TESTS, STRONG_TESTS])

    assert record["status"] == "COMPLETE"
    assert record["attempts"] == 2 and record["calls_cloud"] == 2
    assert record["target_met"] and record["usable"]
    assert record["best_attempt"] == 2
    assert record["line_coverage"] == 100.0 and record["branch_coverage"] == 100.0
    assert record["heldout_strict_ms"] == 100.0 and record["feedback_strict_ms"] == 100.0

    feedback_prompt = providers["cloud"].requests[1].user
    assert "test_wrong: AssertionError" in feedback_prompt
    assert "These lines and branches of the module are not executed" in feedback_prompt
    assert "Change 1:" in feedback_prompt and "+    if n <= 0:" in feedback_prompt
    # Held-out isolation (FB11): the surviving held-out mutant never reaches a prompt.
    assert HELDOUT_MARKER not in feedback_prompt

    results = store.dir
    for name in ("calls.jsonl", "attempts.jsonl", "mutant_results.jsonl", "runs.jsonl"):
        assert (results / name).is_file(), name
    assert len(read_jsonl(results / "calls.jsonl")) == 2
    attempts = read_jsonl(results / "attempts.jsonl")
    assert attempts[0]["error_type"] == "TEST_FAIL" and attempts[0]["removed_tests"] == ["test_wrong"]
    assert attempts[0]["mutant_counts"]["heldout"]["SURVIVED_NOT_COVERED"] == 1

    artifacts = results / "artifacts" / record["run_id"]
    for name in (
        "call1_prompt.txt", "call1_response.txt", "call2_prompt.txt", "attempt1_tests_raw.py",
        "attempt1_tests_filtered.py", "attempt1_feedback.json", "final_tests.py", "run.log",
    ):
        assert (artifacts / name).is_file(), name
    log = (artifacts / "run.log").read_text(encoding="utf-8")
    assert all(line.startswith("[") for line in log.splitlines())

    trace = export_trace(results, record["run_id"], tmp_path / "trace")
    for name in (
        "00_input_function.py", "01_C2_subject.json", "02_C3_to_C4_prompt_request.json", "03_prompt_sent.txt",
        "04_C5_raw_response.txt", "05_C5_to_C6_llm_result.json", "06_C6_parsed_tests.py",
        "06_C6_parse_report.json", "07_C7_static_validation.json", "08_C8_test_results.json",
        "08_C8_coverage.json", "09_C9_mutation_feedback_view.json", "09_C9_mutation_full_results.json",
        "10_C10_decision.json", "11_C10_to_C3_feedback_bundle.json", "attempt2_03_prompt_sent.txt",
        "attempt2_10_C10_decision.json", "20_final_tests.py", "21_run_record.json", "run.log", "TRACE_README.md",
    ):
        assert (trace / name).is_file(), name
    view = json.loads((trace / "09_C9_mutation_feedback_view.json").read_text(encoding="utf-8"))
    assert all(r["set_label"] == "feedback" for r in view["results"])


def test_esc_escalates_after_two_local_calls(tmp_path: Path, subject) -> None:
    """ESC: two weak local suites miss the target, so call 3 goes to the cloud model."""
    record, _store, providers = _run(
        tmp_path, subject, "ESC", local=[NEGATIVE_ONLY_TESTS, NEGATIVE_ONLY_TESTS], cloud=[STRONG_TESTS]
    )
    assert record["status"] == "COMPLETE"
    assert record["escalated"] is True and "target not met after 2 local calls" in record["escalation_reason"]
    assert (record["calls_local"], record["calls_cloud"]) == (2, 1)
    assert record["target_met"] and record["heldout_strict_ms"] == 100.0
    assert providers["cloud"].requests[0].call_no == 3
    # The cloud model receives the best local suite through T2.
    assert "def test_negative" in providers["cloud"].requests[0].user


def test_esc_does_not_escalate_when_local_meets_target(tmp_path: Path, subject) -> None:
    """ESC stops after call 1 when the local suite already meets the target; the cloud is never called."""
    record, _store, providers = _run(tmp_path, subject, "ESC", local=[STRONG_TESTS], cloud=[])
    assert record["escalated"] is False and record["calls_cloud"] == 0
    assert providers["cloud"].requests == []


def test_no_code_reply_gets_nocode_block(tmp_path: Path, subject) -> None:
    """A reply without code is recorded as NO_CODE and the next prompt says so."""
    no_code = canned("no_code", "classify", "classify")
    record, _store, providers = _run(tmp_path, subject, "C-F1", cloud=[no_code, STRONG_TESTS])
    assert record["attempt_error_types"][0] == "NO_CODE"
    second = providers["cloud"].requests[1].user
    assert "did not contain a usable Python test file (NO_CODE)" in second
    assert "# no test file was returned" in second


def test_infra_error_marks_run_for_requeue(tmp_path: Path, subject) -> None:
    """An INFRA failure ends the run with status INFRA_ERROR and is never a model error."""
    from testroute.llm.base import InfraError

    record, store, _providers = _run(tmp_path, subject, "C-F0", cloud=[InfraError("HTTP 503")])
    assert record["status"] == "INFRA_ERROR"
    calls = read_jsonl(store.dir / "calls.jsonl")
    assert calls[0]["error_class"] == "INFRA"


def test_f0_makes_a_single_call(tmp_path: Path, subject) -> None:
    """F0 conditions use exactly one call even when the target is not met."""
    record, _store, providers = _run(tmp_path, subject, "L-F0", local=[WEAK_TESTS, STRONG_TESTS])
    assert record["calls_total"] == 1 and len(providers["local"].requests) == 1
    assert record["usable"] and record["tests_failing"] == 1


def test_full_loop_with_real_mutation(tmp_path: Path, subject) -> None:
    """C-F4 with the real AST mutator and runner: weak tests leave survivors, strong tests kill every mutant."""
    from testroute.mutation.mutant_set import build_mutant_set
    from testroute.mutation.runner import MutantRunner

    store = ResultsStore(tmp_path / "results")
    config = load_config("experiment_main.yaml")
    mutant_set = build_mutant_set(subject.function_id, subject.module_name, subject.entry_point, subject.source)
    provider = FakeProvider([WEAK_TESTS, STRONG_TESTS])
    record = RunController(
        subject=subject,
        condition=parse_condition("C-F4"),
        trial=1,
        providers={"cloud": provider},
        analyzer=MutantRunner.from_config(mutant_set, store, config["mutation"]),
        store=store,
        config=config,
    ).execute()
    assert record["status"] == "COMPLETE" and record["attempts"] == 2
    assert record["heldout_strict_ms"] == 100.0
    assert "Change 1:" in provider.requests[1].user
    heldout_lines = {m.mutated_line for m in mutant_set.scoring() if m.set_label == "heldout"}
    feedback_lines = {m.mutated_line for m in mutant_set.scoring() if m.set_label == "feedback"}
    for line in heldout_lines - feedback_lines:
        assert f"+{line}\n" not in provider.requests[1].user
