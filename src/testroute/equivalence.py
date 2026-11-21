"""Equivalence pipeline for the selected subjects: prepare, review, finalize.

Component: C9 (Mutation Analyzer), preparation: the ``prepare-mutants`` and
``review-equivalence`` commands.

Responsibility (spec Section 6.4, FB2), for every mutant of every selected
subject, before the pilot:

    prepare-mutants
        Generates mutants (6.1), drops TCE_EQUIVALENT and DUPLICATE ones
        (6.2), and runs differential execution of each remaining mutant
        against the original on HumanEval's test inputs plus up to 200
        HumanEval+ inputs (6.4 step 1), labelling it DIFF_OUTPUT,
        DIFF_TIMEOUT_ONLY or NO_DIFF (6.4 step 2). Results go to
        ``experiments/equivalence/differential/<function_id>.json`` and a
        summary CSV.

    review-equivalence
        Writes ``experiments/equivalence/review_<function_id>.md`` per
        function with NO_DIFF mutants (original function, diff, differential
        evidence) and one ``equivalence_review.csv`` with columns
        function_id, mutant_id, operator, diff, reviewer_1, reviewer_2,
        final_label, justification (6.4 step 3). The label columns are left
        EMPTY: they are filled by the two students by hand, never by this
        code. An existing CSV is never overwritten: rows that already exist
        keep every label, and only missing rows are added.

    prepare-mutants --finalize (after the manual review)
        Requires a final_label (EQUIVALENT, NOT_EQUIVALENT or UNSURE) for
        every NO_DIFF mutant and a justification for NOT_EQUIVALENT, then
        applies the labels, the cap (6.3) and the split (6.5) and writes the
        final ``experiments/mutants/<function_id>.json`` (6.4 step 4) and
        ``results/main/analysis/equivalence_summary.csv`` (6.4 step 5).

    The benchmark inputs are used only here and in screening; they are never
    shown to models and never used to score generated tests.

Receives: selection.csv, bundled inputs, the review CSV.
Produces: the files above.
Talks to: ``datasets``, ``differential``, ``mutation.mutant_set``,
    ``subject_selection``.
Spec sections: 6.2 to 6.5.
Feedback IDs: FB2 (complete equivalence pipeline, UNSURE kept for the
    sensitivity analysis).
"""

from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from testroute import paths
from testroute.datasets import load_inputs, load_problems
from testroute.differential import DIFF_OUTPUT, DIFF_TIMEOUT_ONLY, NO_DIFF, compare, execute, usable_inputs
from testroute.mutation.mutant_set import Mutant, MutantSet, build_mutant_set
from testroute.mutation.results import EQUIVALENT, NOT_EQUIVALENT, NOT_REVIEWED, UNSURE
from testroute.mutation.runner import default_workers
from testroute.settings import load_config
from testroute.subject_selection import selected_subjects

DIFFERENTIAL_DIR = paths.EQUIVALENCE_DIR / "differential"
REVIEW_CSV = paths.EQUIVALENCE_DIR / "equivalence_review.csv"
REVIEW_COLUMNS = ("function_id", "mutant_id", "operator", "diff", "reviewer_1", "reviewer_2", "final_label", "justification")
REVIEW_LABELS = (EQUIVALENT, NOT_EQUIVALENT, UNSURE)
SUMMARY_COLUMNS = (
    "function_id", "group", "generated", "TCE_EQUIVALENT", "DUPLICATE", DIFF_OUTPUT, DIFF_TIMEOUT_ONLY,
    NO_DIFF, EQUIVALENT, NOT_EQUIVALENT, UNSURE, "capped_out", "scoring", "feedback", "heldout",
)

# A mutant that already timed out this often is non-equivalent; its remaining
# inputs are skipped to keep the pipeline fast (see differential.py).
MAX_MUTANT_TIMEOUTS = 3


class ReviewIncomplete(Exception):
    """The manual review is missing labels; the message lists what is missing."""


def _mutation_settings() -> dict[str, Any]:
    return load_config("experiment_main.yaml")["mutation"]


def compact_diff(mutant: Mutant) -> str:
    """One-line diff for the CSV: ``line N: - old | + new``."""
    return f"line {mutant.line}: - {mutant.original_line.strip()} | + {mutant.mutated_line.strip()}"


def prepare_mutants(workers: int | None = None, function_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Run 6.1, 6.2 and the differential step of 6.4 for every selected subject.

    Args:
        workers: Parallel differential executions; default half the CPUs.
        function_ids: Restrict to these subjects (default: all 35).

    Returns:
        One summary dict per subject.

    Side effects:
        Starts sandboxed subprocesses; writes the differential files and
        ``experiments/equivalence/differential_summary.csv``.
    """
    problems = {p.function_id: p for p in load_problems()}
    inputs = load_inputs()
    cfg = _mutation_settings()
    rows = selected_subjects()
    if function_ids:
        rows = [r for r in rows if r["function_id"] in function_ids]
    DIFFERENTIAL_DIR.mkdir(parents=True, exist_ok=True)
    summaries = []
    with ThreadPoolExecutor(max_workers=workers or default_workers()) as pool:
        for row in rows:
            problem = problems[row["function_id"]]
            data = inputs.get(problem.function_id, [])
            mutant_set = build_mutant_set(
                problem.function_id, problem.function_id, problem.entry_point, problem.source,
                cap=int(cfg["mutant_cap"]), cap_seed=int(cfg["cap_seed"]), split_seed=int(cfg["split_seed"]),
                prompt_seed=int(cfg["prompt_mutant_seed"]),
            )
            original = execute(problem.function_id, problem.entry_point, problem.source, data)
            candidates = [m for m in mutant_set.mutants if not m.static_label]
            traces = pool.map(
                lambda m: execute(problem.function_id, problem.entry_point, m.source, data, max_timeouts=MAX_MUTANT_TIMEOUTS),
                candidates,
            )
            verdicts = {m.mutant_id: compare(original, t).to_dict() for m, t in zip(candidates, traces)}
            record = {
                "function_id": problem.function_id,
                "group": row["group"],
                "inputs_total": len(data),
                "inputs_usable": usable_inputs(original),
                "original_timeouts": original.timeouts(),
                "mutants": verdicts,
            }
            (DIFFERENTIAL_DIR / f"{problem.function_id}.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
            counts = {label: sum(1 for v in verdicts.values() if v["label"] == label) for label in (DIFF_OUTPUT, DIFF_TIMEOUT_ONLY, NO_DIFF)}
            summaries.append({
                "function_id": problem.function_id, "group": row["group"], "generated": len(mutant_set.mutants),
                "TCE_EQUIVALENT": sum(1 for m in mutant_set.mutants if m.static_label == "TCE_EQUIVALENT"),
                "DUPLICATE": sum(1 for m in mutant_set.mutants if m.static_label == "DUPLICATE"),
                **counts, "inputs_usable": record["inputs_usable"],
            })
    with (paths.EQUIVALENCE_DIR / "differential_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]) if summaries else ["function_id"])
        writer.writeheader()
        writer.writerows(summaries)
    return summaries


def _load_differential(function_id: str) -> dict[str, Any]:
    path = DIFFERENTIAL_DIR / f"{function_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no differential results for {function_id}; run prepare-mutants first")
    return json.loads(path.read_text(encoding="utf-8"))


def _mutants_of(function_id: str) -> tuple[Any, MutantSet]:
    problem = {p.function_id: p for p in load_problems()}[function_id]
    cfg = _mutation_settings()
    return problem, build_mutant_set(problem.function_id, problem.function_id, problem.entry_point, problem.source,
                                     cap=int(cfg["mutant_cap"]), cap_seed=int(cfg["cap_seed"]),
                                     split_seed=int(cfg["split_seed"]), prompt_seed=int(cfg["prompt_mutant_seed"]))


def read_review(path: Path | None = None) -> list[dict[str, str]]:
    """Rows of the review CSV (empty if it does not exist)."""
    target = path or REVIEW_CSV
    if not target.is_file():
        return []
    with target.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass
class ReviewResult:
    """What review-equivalence wrote."""

    functions_with_candidates: int
    rows_total: int
    rows_added: int
    markdown_files: list[Path]


def review_equivalence(review_csv: Path | None = None) -> ReviewResult:
    """Write the manual review files (spec 6.4 step 3). Labels stay empty.

    Side effects:
        Writes review_<function_id>.md files and the review CSV. Existing CSV
        rows, and every label in them, are kept unchanged.
    """
    target = review_csv or REVIEW_CSV
    existing = read_review(target)
    known = {(r["function_id"], r["mutant_id"]) for r in existing}
    rows = list(existing)
    added = 0
    files: list[Path] = []
    for subject in selected_subjects():
        function_id = subject["function_id"]
        differential = _load_differential(function_id)
        problem, mutant_set = _mutants_of(function_id)
        by_id = {m.mutant_id: m for m in mutant_set.mutants}
        candidates = [by_id[m] for m, v in sorted(differential["mutants"].items()) if v["label"] == NO_DIFF]
        if not candidates:
            continue
        for mutant in candidates:
            if (function_id, mutant.mutant_id) not in known:
                rows.append({c: "" for c in REVIEW_COLUMNS} | {
                    "function_id": function_id, "mutant_id": mutant.mutant_id,
                    "operator": mutant.operator, "diff": compact_diff(mutant),
                })
                added += 1
        files.append(_write_review_markdown(problem, candidates, differential))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return ReviewResult(len(files), len(rows), added, files)


def _write_review_markdown(problem: Any, candidates: list[Mutant], differential: dict[str, Any]) -> Path:
    """One Markdown review sheet for a function's NO_DIFF mutants."""
    lines = [
        f"# Equivalence review: {problem.function_id} ({problem.task_id}, `{problem.entry_point}`)",
        "",
        "Each mutant below produced the same outputs as the original on every input of differential",
        f"execution ({differential['inputs_usable']} of {differential['inputs_total']} inputs usable: HumanEval's own test",
        "inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in",
        "`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).",
        "",
        "Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a",
        "distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.",
        "",
        "## Original function",
        "",
        "```python",
        problem.source.rstrip("\n"),
        "```",
        "",
    ]
    for mutant in candidates:
        evidence = differential["mutants"][mutant.mutant_id]
        lines += [
            f"## {mutant.mutant_id} ({mutant.operator}: {mutant.description}), line {mutant.line}",
            "",
            "```diff",
            mutant.diff.rstrip("\n"),
            "```",
            "",
            f"Differential evidence: {evidence['inputs_compared']} inputs compared, all outputs and argument states equal"
            + (f"; {evidence['equal_exceptions']} of them raised the same exception type in both versions" if evidence["equal_exceptions"] else "")
            + ".",
            "",
            "- reviewer_1: ",
            "- reviewer_2: ",
            "- final_label: ",
            "- justification: ",
            "",
        ]
    path = paths.EQUIVALENCE_DIR / f"review_{problem.function_id}.md"
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


def finalize_mutants(review_csv: Path | None = None) -> list[dict[str, Any]]:
    """Apply review labels, cap and split; write final mutant files (6.4 steps 4 and 5).

    Raises:
        ReviewIncomplete: when a NO_DIFF mutant has no valid final_label or a
            NOT_EQUIVALENT label has no justification.

    Side effects:
        Writes experiments/mutants/<function_id>.json and
        results/main/analysis/equivalence_summary.csv.
    """
    labels = {(r["function_id"], r["mutant_id"]): r for r in read_review(review_csv)}
    problems_found: list[str] = []
    plans: list[tuple[dict[str, str], dict[str, tuple[str, str]]]] = []
    for subject in selected_subjects():
        function_id = subject["function_id"]
        differential = _load_differential(function_id)
        equivalence: dict[str, tuple[str, str]] = {}
        for mutant_id, verdict in differential["mutants"].items():
            label = verdict["label"]
            if label != NO_DIFF:
                equivalence[mutant_id] = (label, NOT_REVIEWED)
                continue
            row = labels.get((function_id, mutant_id))
            final = (row or {}).get("final_label", "").strip().upper()
            if final not in REVIEW_LABELS:
                problems_found.append(f"{function_id} {mutant_id}: final_label missing or not one of {', '.join(REVIEW_LABELS)}")
            elif final == NOT_EQUIVALENT and not (row or {}).get("justification", "").strip():
                problems_found.append(f"{function_id} {mutant_id}: NOT_EQUIVALENT needs a distinguishing input in justification")
            equivalence[mutant_id] = (label, final)
        plans.append((subject, equivalence))
    if problems_found:
        raise ReviewIncomplete("\n".join(problems_found))

    cfg = _mutation_settings()
    summaries = []
    for subject, equivalence in plans:
        problem = {p.function_id: p for p in load_problems()}[subject["function_id"]]
        mutant_set = build_mutant_set(
            problem.function_id, problem.function_id, problem.entry_point, problem.source,
            cap=int(cfg["mutant_cap"]), cap_seed=int(cfg["cap_seed"]), split_seed=int(cfg["split_seed"]),
            prompt_seed=int(cfg["prompt_mutant_seed"]), equivalence=equivalence,
        )
        mutant_set.save(paths.MUTANTS_DIR / f"{problem.function_id}.json")
        counts = mutant_set.stage_counts()
        summaries.append({"function_id": problem.function_id, "group": subject["group"]} | {c: counts.get(c, 0) for c in SUMMARY_COLUMNS[2:]})
    out = paths.RESULTS_MAIN_DIR / "analysis" / "equivalence_summary.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(summaries)
        totals = {c: sum(int(s[c]) for s in summaries) for c in SUMMARY_COLUMNS[2:]}
        writer.writerow({"function_id": "TOTAL", "group": ""} | totals)
    return summaries
