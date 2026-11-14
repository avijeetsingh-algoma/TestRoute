"""Unit tests for scores (C9), held-out isolation, cost, and redaction (C11).

Spec Section 14: strict and lenient scores, cost calculation, held-out
isolation, secret redaction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from testroute import cost
from testroute.logging_setup import REDACTED, RunLog, redact
from testroute.mutation.results import (
    SET_FEEDBACK,
    SET_HELDOUT,
    FeedbackMutationView,
    HeldOutLeakError,
    MutantInfo,
    MutantResult,
)
from testroute.mutation.scores import compute_scores
from testroute.records import ResultsStore

FAKE_KEY = "sk-abcdefghijklmnop1234567890"


def _r(mutant_id: str, outcome: str, diag: str = "") -> MutantResult:
    return MutantResult(mutant_id, SET_HELDOUT, outcome, 0.1, diag)


def test_strict_lenient_raw_and_unsure_scores() -> None:
    """The four spec 6.7 scores on a hand-computed example.

    Set of 6 after dropping ENV_ERROR: m1 m2 KILLED, m3 TIMEOUT, m4 SURVIVED (UNSURE),
    m5 SURVIVED (EQUIVALENT), m6 SURVIVED_NOT_COVERED. Strict 2/5, lenient 3/5, raw 2/6, UNSURE-excluded 2/4.
    """
    results = [
        _r("m1", "KILLED"), _r("m2", "KILLED"), _r("m3", "TIMEOUT", "likely_genuine"),
        _r("m4", "SURVIVED"), _r("m5", "SURVIVED"), _r("m6", "SURVIVED_NOT_COVERED"), _r("m7", "ENV_ERROR"),
    ]
    scores = compute_scores(results, {"m4": "UNSURE", "m5": "EQUIVALENT"})
    assert (scores.strict, scores.lenient, scores.raw, scores.unsure_excluded) == (40.0, 60.0, 33.33, 50.0)
    assert scores.size == 6 and scores.counts["ENV_ERROR"] == 1
    assert scores.timeouts_by_diagnosis == {"likely_genuine": 1}


def test_empty_set_scores_are_undefined() -> None:
    """A set with no scoring mutants has undefined (None) scores, not 0 or 100."""
    assert compute_scores([], {}).strict is None


def test_feedback_view_rejects_heldout() -> None:
    """The controller's only mutation object cannot be built with held-out data (FB11)."""
    with pytest.raises(HeldOutLeakError):
        FeedbackMutationView(results=(_r("m1", "KILLED"),))


def test_feedback_view_never_offers_timeout_or_equivalent() -> None:
    """TIMEOUT and EQUIVALENT mutants are never shown as survivors (spec Section 7)."""
    infos = {
        k: MutantInfo(k, "ROR", 1, "a", "b", SET_FEEDBACK, equivalence="EQUIVALENT" if k == "e" else "NOT_EQUIVALENT")
        for k in ("s", "t", "e")
    }
    view = FeedbackMutationView(
        results=(
            MutantResult("s", SET_FEEDBACK, "SURVIVED"),
            MutantResult("t", SET_FEEDBACK, "TIMEOUT"),
            MutantResult("e", SET_FEEDBACK, "SURVIVED"),
        ),
        mutants=infos,
        prompt_order=("t", "e", "s"),
    )
    assert [m.mutant_id for m in view.survivors_for_prompt(5)] == ["s"]


def test_cost_list_and_off_peak() -> None:
    """List cost uses list rates; an off-peak call uses off-peak rates for the time-of-day estimate."""
    pricing = cost.load_pricing()
    call = {"cache_miss_tokens": 1_000_000, "cache_hit_tokens": 1_000_000, "output_tokens": 1_000_000,
            "started_utc": "2026-11-23T20:00:00+00:00"}
    list_cost, tod = cost.cloud_call_costs(call, pricing)
    assert list_cost == pytest.approx(0.30 + 0.006 + 1.20)
    assert tod == pytest.approx(0.15 + 0.003 + 0.60)
    assert cost.is_peak("", pricing)


@pytest.mark.parametrize(
    ("utc", "peak"),
    [
        ("2026-11-23T00:59:59+00:00", False),  # Monday, before the first window
        ("2026-11-23T01:00:00+00:00", True),   # Monday, first window opens
        ("2026-11-23T03:59:00+00:00", True),   # last minute of the first window
        ("2026-11-23T04:00:00+00:00", False),  # gap between the windows
        ("2026-11-23T05:30:00+00:00", False),
        ("2026-11-23T06:00:00+00:00", True),   # second window opens
        ("2026-11-27T09:59:00+00:00", True),   # Friday, last minute of the second window
        ("2026-11-27T10:00:00+00:00", False),  # second window closed
        ("2026-11-28T02:00:00+00:00", False),  # Saturday: weekends are off-peak
        ("2026-11-29T07:00:00+00:00", False),  # Sunday
        ("2026-11-23T10:00:00-05:00", False),  # 15:00 UTC given in another offset
    ],
)
def test_peak_window(utc: str, peak: bool) -> None:
    """Peak is 01:00 to 04:00 and 06:00 to 10:00 UTC on Monday to Friday; everything else is off-peak."""
    assert cost.is_peak(utc, cost.load_pricing()) is peak


def test_energy_estimate() -> None:
    """One hour at the assumed 90 W is 0.09 kWh, priced at the assumed rate."""
    kwh, usd = cost.energy_estimate(3600, cost.load_pricing())
    assert kwh == pytest.approx(0.09) and usd == pytest.approx(0.09 * 0.15)


def test_redaction_of_key_shapes_and_configured_secret() -> None:
    """Key-shaped strings and explicitly given secrets are replaced before writing."""
    text = f"Authorization: Bearer {FAKE_KEY} and key {FAKE_KEY} and custom hunter2secret"
    out = redact(text, extra_secrets=("hunter2secret",))
    assert FAKE_KEY not in out and "hunter2secret" not in out and REDACTED in out


def test_records_and_logs_are_redacted(tmp_path: Path) -> None:
    """A key that appears in a record, an artifact or a log line never reaches disk."""
    store = ResultsStore(tmp_path)
    store.write_call({"run_id": "r", "note": FAKE_KEY})
    store.write_artifact("r", "call1_response.txt", f"key={FAKE_KEY}")
    RunLog(tmp_path / "run.log").log("C5", f"using {FAKE_KEY}")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert FAKE_KEY not in path.read_text(encoding="utf-8"), path


def test_store_merges_heldout_counts_not_controller(tmp_path: Path) -> None:
    """Held-out counts reach attempts.jsonl through the sink, not through the record the controller passes."""
    store = ResultsStore(tmp_path)
    info = {"h": MutantInfo("h", "ROR", 1, "a", "b", SET_HELDOUT)}
    store.record_attempt_mutants("r", 1, [MutantResult("h", SET_HELDOUT, "SURVIVED")], info)
    store.write_attempt({"run_id": "r", "attempt_no": 1})
    record = json.loads((tmp_path / "attempts.jsonl").read_text(encoding="utf-8"))
    assert record["mutant_counts"]["heldout"]["SURVIVED"] == 1
