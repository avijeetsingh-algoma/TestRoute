"""Plain-text results store: JSONL records and per-run artifact folders.

Component: C11 (Results Logger), records part.

Responsibility:
    Writes everything spec Section 10 lists, as plain text (spec rule 1.4):
    * ``calls.jsonl``, ``attempts.jsonl``, ``mutant_results.jsonl`` and
      ``runs.jsonl`` in the results folder, one JSON object per line;
    * ``artifacts/<run_id>/`` with prompts, responses, raw and filtered test
      files, feedback bundles, the final suite and ``run.log``.
    Every string is passed through :func:`logging_setup.redact` first.

    It is also the :class:`MutationSink` of the held-out isolation design
    (spec Section 5, FB11): mutation analyzers send complete results (both
    sets) here, and this store, not the controller, merges held-out counts
    into attempt records and held-out scores into the run record.

Receives: records from the controller (C10); mutant results from C9.
Produces: files in the results folder.
Talks to: ``logging_setup`` (redaction), ``trace`` (trace file names),
    ``mutation.scores``; read later by C1 (resume) and C12 (analysis).
Spec sections: 1.4, 5 (C11), 6.7 (scores in records), 10.
Feedback IDs: FB2 (equivalence-aware scores), FB3 (timeout counts),
    FB11 (held-out data enters only here).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from testroute import paths
from testroute.logging_setup import redact
from testroute.mutation.results import SET_FEEDBACK, SET_HELDOUT, MutantInfo, MutantResult
from testroute.mutation.scores import SetScores, compute_scores
from testroute.trace import TraceWriter, read_jsonl

CALLS_FILE = "calls.jsonl"
ATTEMPTS_FILE = "attempts.jsonl"
MUTANTS_FILE = "mutant_results.jsonl"
RUNS_FILE = "runs.jsonl"

# Run status values (D2 Section 9 failure classes at run level).
STATUS_COMPLETE = "COMPLETE"
STATUS_INFRA_ERROR = "INFRA_ERROR"
STATUS_TOOL_ERROR = "TOOL_ERROR"

SETS: tuple[str, ...] = (SET_HELDOUT, SET_FEEDBACK, "all")


def git_commit() -> str:
    """Commit hash of the project, or ``unknown`` outside a git checkout.

    A grader who extracts the ZIP has no ``.git`` folder; the run record then
    says ``unknown`` and the hash is documented in REPRODUCIBILITY.md.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(paths.PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "unknown"


def config_hash(config: dict[str, Any]) -> str:
    """Short SHA-256 of a resolved config, independent of key order."""
    text = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def scores_dict(scores: SetScores) -> dict[str, Any]:
    """Flatten SetScores for a record."""
    return asdict(scores)


class ResultsStore:
    """Writes records and artifacts for one results folder.

    Also implements the MutationSink protocol of ``mutation.results``.
    """

    def __init__(self, results_dir: Path) -> None:
        """Args: results_dir: e.g. ``results/main``. Created if missing."""
        self.dir = results_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        # Mutation results kept in memory for the runs of this process, so
        # attempt and run records can be completed without the controller
        # seeing held-out data. Keys: (run_id, attempt_no) and run_id.
        self._attempt_mutants: dict[tuple[str, int], tuple[list[MutantResult], dict[str, MutantInfo]]] = {}
        self._final_mutants: dict[str, tuple[list[MutantResult], dict[str, MutantInfo]]] = {}

    # Folders and plain files

    def run_dir(self, run_id: str) -> Path:
        """Artifact folder of a run (created)."""
        folder = self.dir / "artifacts" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def trace(self, run_id: str) -> TraceWriter:
        """Trace writer for a run (``artifacts/<run_id>/trace``)."""
        return TraceWriter(self.run_dir(run_id) / "trace")

    def write_artifact(self, run_id: str, name: str, content: str) -> Path:
        """Write a redacted text artifact and return its path.

        Returns:
            Absolute path; records store it relative to the results folder.
        """
        path = self.run_dir(run_id) / name
        path.write_text(redact(content), encoding="utf-8", newline="\n")
        return path

    def relative(self, path: Path) -> str:
        """Path relative to the results folder, with forward slashes."""
        return path.relative_to(self.dir).as_posix()

    def _append(self, file_name: str, record: dict[str, Any]) -> None:
        """Append one redacted JSON line. One write call per line keeps lines whole."""
        line = redact(json.dumps(record, ensure_ascii=False, default=str))
        with (self.dir / file_name).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    # Records written by the controller

    def write_call(self, record: dict[str, Any]) -> None:
        """Append a calls.jsonl record."""
        self._append(CALLS_FILE, record)

    def write_attempt(self, record: dict[str, Any]) -> None:
        """Append an attempts.jsonl record, adding mutant counts of every set.

        The controller passes the record without held-out data; the counts per
        set (held-out included) are merged in here (FB11).
        """
        key = (record["run_id"], int(record["attempt_no"]))
        if key in self._attempt_mutants:
            results, mutants = self._attempt_mutants[key]
            record = dict(record, mutant_counts={s: self._set_scores(results, mutants, s).counts for s in SETS})
        self._append(ATTEMPTS_FILE, record)

    def write_run(self, record: dict[str, Any], usable: bool) -> dict[str, Any]:
        """Complete and append a runs.jsonl record.

        Adds the spec 6.7 scores of every set for the final suite. A run with
        no usable suite scores 0 (spec Section 8), not "undefined".

        Args:
            record: Run record from the controller (no mutation scores).
            usable: Whether the final suite has at least one valid test.

        Returns:
            The completed record (also written to the trace as file 21).
        """
        run_id = record["run_id"]
        final = self._final_mutants.get(run_id)
        scores: dict[str, Any] = {}
        for set_name in SETS:
            if usable and final is not None:
                scores[set_name] = scores_dict(self._set_scores(final[0], final[1], set_name))
            else:
                scores[set_name] = {"strict": 0.0, "lenient": 0.0, "raw": 0.0, "unsure_excluded": 0.0}
        record = dict(record, mutation_scores=scores)
        heldout = scores[SET_HELDOUT]
        # Flat copies of the headline numbers make the CSV export and the
        # analysis simpler; the nested block keeps every detail.
        record.update(
            heldout_strict_ms=heldout.get("strict"),
            heldout_lenient_ms=heldout.get("lenient"),
            heldout_raw_ms=heldout.get("raw"),
            heldout_unsure_excluded_ms=heldout.get("unsure_excluded"),
            feedback_strict_ms=scores[SET_FEEDBACK].get("strict"),
            all_strict_ms=scores["all"].get("strict"),
        )
        self._append(RUNS_FILE, record)
        self.trace(run_id).json("21_run_record.json", record)
        return record

    # MutationSink implementation (called by C9 analyzers only)

    def record_attempt_mutants(
        self, run_id: str, attempt_no: int, results: list[MutantResult], mutants: dict[str, MutantInfo]
    ) -> None:
        """Store all mutant results of an attempt and write them to disk."""
        self._attempt_mutants[(run_id, attempt_no)] = (list(results), dict(mutants))
        self._write_mutant_lines(run_id, attempt_no, results)
        self.trace(run_id).json(
            "09_C9_mutation_full_results.json",
            {
                "scores": {s: scores_dict(self._set_scores(results, mutants, s)) for s in SETS},
                "results": [asdict(r) for r in results],
            },
            attempt_no,
        )

    def record_final_mutants(self, run_id: str, results: list[MutantResult], mutants: dict[str, MutantInfo]) -> None:
        """Store mutant results of the final suite (attempt label ``final``)."""
        self._final_mutants[run_id] = (list(results), dict(mutants))
        self._write_mutant_lines(run_id, "final", results)

    def reuse_attempt_as_final(self, run_id: str, attempt_no: int) -> None:
        """Use an attempt's results as final when the final suite is unchanged."""
        results, mutants = self._attempt_mutants[(run_id, attempt_no)]
        self.record_final_mutants(run_id, results, mutants)

    def _write_mutant_lines(self, run_id: str, attempt_no: int | str, results: list[MutantResult]) -> None:
        for r in results:
            self._append(
                MUTANTS_FILE,
                {
                    "run_id": run_id,
                    "attempt_no": attempt_no,
                    "mutant_id": r.mutant_id,
                    "set": r.set_label,
                    "outcome": r.outcome,
                    "duration_s": round(r.duration_s, 4),
                    "timeout_diagnosis": r.timeout_diagnosis,
                },
            )

    @staticmethod
    def _set_scores(results: list[MutantResult], mutants: dict[str, MutantInfo], set_name: str) -> SetScores:
        chosen = results if set_name == "all" else [r for r in results if r.set_label == set_name]
        return compute_scores(chosen, {m.mutant_id: m.equivalence for m in mutants.values()})

    # Reading back

    def completed_run_ids(self) -> set[str]:
        """Run ids whose latest record is COMPLETE (used for resuming, C1)."""
        latest: dict[str, str] = {}
        for record in read_jsonl(self.dir / RUNS_FILE):
            latest[record["run_id"]] = record.get("status", "")
        return {run_id for run_id, status in latest.items() if status == STATUS_COMPLETE}
