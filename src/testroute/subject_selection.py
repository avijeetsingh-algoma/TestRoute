"""Automated screening of HumanEval and the seeded draw of 35 subjects.

Component: C2 (Subject Loader and Input Validator), experiment preparation:
the ``prepare-subjects`` command.

Responsibility (spec Section 9.1, locked):
    Screens all 164 HumanEval problems. A problem is eligible when it passes
    every criterion below, checked in this order; the first failure is
    recorded and the more expensive later checks are skipped for it:
    1. ``parses``: the module (prompt + canonical solution) parses and defines
       the entry point as a top-level function;
    2. ``no_io_clock_random``: no file, network, clock or randomness use
       (static scan of imports and calls);
    3. ``max_80_lines``: at most 80 source lines;
    4. ``min_2_decisions``: at least 2 decision points counted on the AST of
       the entry point and its helpers (if/elif, for, while, conditional
       expressions, extra operands of and/or, comprehension conditions,
       except handlers, match cases);
    5. ``import_under_1s``: imports in under 1 s in a sandboxed subprocess;
    6. ``passes_humaneval_tests``: the reference passes HumanEval's own tests;
    7. ``deterministic``: identical outputs on all bundled inputs in two runs
       with different hash seeds (dynamic part of the determinism criterion);
    8. ``min_10_mutants``: at least 10 mutants remain after TCE and duplicate
       removal (6.2); at this stage NO_DIFF mutants count as non-equivalent
       (spec 9.1), so differential execution is not needed here.

    Draw: eligible problems are ranked by mutant count and split into three
    equal bands (easy = fewest mutants, medium, hard). 35 are drawn with seed
    20261005, allocated to bands in proportion to band size (largest
    remainder), and 5 of the 35 go to the pilot, again allocated across bands
    in proportion. Outputs:
    * ``experiments/subjects/functions/<HE_id>.py`` for the 35 subjects,
      exactly prompt + canonical solution (the file is what models see);
    * ``experiments/subjects/selection.csv`` listing every problem with each
      criterion's result, its band and its group (main, pilot or blank).

Receives: the bundled HumanEval data (``datasets``).
Produces: the files above; returns the selection rows.
Talks to: ``subjects`` (import check), ``mutation.mutant_set`` (mutant
    counts), ``differential`` (determinism), ``sandbox`` (HumanEval tests).
Spec sections: 9.1.
Feedback IDs: FB8 (subject choice), FB9 (fully automated).
"""

from __future__ import annotations

import ast
import csv
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from testroute import paths
from testroute.datasets import Problem, load_inputs, load_problems
from testroute.differential import execute, same_record
from testroute.mutation.mutant_set import build_mutant_set
from testroute.mutation.operators import target_functions
from testroute.sandbox import Workspace, build_sandbox_env, run_process
from testroute.subjects import SubjectError, load_subject_source

SELECTION_SEED = 20261005
TOTAL_SUBJECTS = 35
PILOT_SUBJECTS = 5
MAX_LINES = 80
MIN_DECISIONS = 2
MIN_MUTANTS = 10
BANDS = ("easy", "medium", "hard")
HUMANEVAL_TEST_TIMEOUT_S = 60.0

CRITERIA: tuple[str, ...] = (
    "parses",
    "no_io_clock_random",
    "max_80_lines",
    "min_2_decisions",
    "import_under_1s",
    "passes_humaneval_tests",
    "deterministic",
    "min_10_mutants",
)

# Modules that read files, use the network, read the clock or produce random
# values (criterion 2). Pure helpers such as math, hashlib or string are fine.
IO_CLOCK_RANDOM_MODULES = frozenset(
    {"os", "io", "pathlib", "shutil", "glob", "tempfile", "socket", "urllib", "http", "requests",
     "subprocess", "time", "datetime", "random", "secrets", "uuid"}
)
IO_NAMES = frozenset({"open", "input"})

SELECTION_FILE = paths.SUBJECTS_DIR / "selection.csv"
CSV_COLUMNS = (
    "task_id", "function_id", "entry_point", "lines", "decision_points", "mutants_after_tce",
    "import_seconds", *CRITERIA, "eligible", "failed_criterion", "band", "group",
)


@dataclass
class Screening:
    """Screening result of one problem (one selection.csv row)."""

    task_id: str
    function_id: str
    entry_point: str
    lines: int = 0
    decision_points: int = 0
    mutants_after_tce: int = 0
    import_seconds: float = 0.0
    checks: dict[str, str] = field(default_factory=dict)
    band: str = ""
    group: str = ""

    @property
    def eligible(self) -> bool:
        return all(self.checks.get(c) == "pass" for c in CRITERIA)

    @property
    def failed_criterion(self) -> str:
        return next((c for c in CRITERIA if self.checks.get(c) == "fail"), "")

    def row(self) -> dict[str, Any]:
        data = {k: v for k, v in asdict(self).items() if k != "checks"}
        data.update({c: self.checks.get(c, "not_checked") for c in CRITERIA})
        data.update(eligible="yes" if self.eligible else "no", failed_criterion=self.failed_criterion)
        return data


def count_decision_points(source: str, entry_point: str) -> int:
    """Decision points of the entry point and its helpers (criterion 4)."""
    total = 0
    for func in target_functions(ast.parse(source), entry_point):
        for node in ast.walk(func):
            if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler)):
                total += 1
            elif isinstance(node, ast.BoolOp):
                total += len(node.values) - 1
            elif isinstance(node, ast.comprehension):
                total += len(node.ifs)
            elif isinstance(node, ast.match_case):
                total += 1
    return total


def uses_io_clock_random(source: str) -> bool:
    """Static part of criterion 2: imports or calls of file, network, clock or random facilities."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] in IO_CLOCK_RANDOM_MODULES for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in IO_CLOCK_RANDOM_MODULES:
            return True
        if isinstance(node, ast.Name) and node.id in IO_NAMES:
            return True
    return False


def passes_humaneval_tests(problem: Problem) -> bool:
    """Run HumanEval's own ``check(candidate)`` on the reference in the sandbox."""
    script = f"{problem.source}\n\n{problem.test}\n\ncheck({problem.entry_point})\nprint('HUMANEVAL_OK')\n"
    with Workspace(prefix="testroute_he_") as ws:
        ws.write("check_reference.py", script)
        proc = run_process([sys.executable, "check_reference.py"], ws.path, build_sandbox_env(ws.path),
                           HUMANEVAL_TEST_TIMEOUT_S)
    return proc.exit_code == 0 and "HUMANEVAL_OK" in proc.stdout


def screen(problem: Problem, inputs: list[list[Any]]) -> Screening:
    """Apply the criteria to one problem, stopping at the first failure."""
    result = Screening(problem.task_id, problem.function_id, problem.entry_point)
    checks = result.checks

    def record(name: str, ok: bool) -> bool:
        checks[name] = "pass" if ok else "fail"
        return ok

    try:
        tree = ast.parse(problem.source)
        defines = any(isinstance(n, ast.FunctionDef) and n.name == problem.entry_point for n in tree.body)
    except SyntaxError:
        defines = False
    if not record("parses", defines):
        return result
    result.lines = len(problem.source.splitlines())
    result.decision_points = count_decision_points(problem.source, problem.entry_point)
    if not record("no_io_clock_random", not uses_io_clock_random(problem.source)):
        return result
    if not record("max_80_lines", result.lines <= MAX_LINES):
        return result
    if not record("min_2_decisions", result.decision_points >= MIN_DECISIONS):
        return result
    try:
        subject = load_subject_source(problem.source, problem.function_id, problem.entry_point,
                                      function_id=problem.function_id)
        result.import_seconds = subject.import_seconds
        import_ok = True
    except SubjectError:
        import_ok = False
    if not record("import_under_1s", import_ok):
        return result
    if not record("passes_humaneval_tests", passes_humaneval_tests(problem)):
        return result
    first = execute(problem.function_id, problem.entry_point, problem.source, inputs, hash_seed=0)
    second = execute(problem.function_id, problem.entry_point, problem.source, inputs, hash_seed=1)
    stable = all(
        same_record(first.results[i], second.results.get(i, {})) or "status" in first.results[i]
        for i in first.results
    )
    if not record("deterministic", stable):
        return result
    mutant_set = build_mutant_set(problem.function_id, problem.function_id, problem.entry_point, problem.source,
                                  cap=10**6)
    result.mutants_after_tce = sum(1 for m in mutant_set.mutants if not m.static_label)
    record("min_10_mutants", result.mutants_after_tce >= MIN_MUTANTS)
    return result


def _allocate(sizes: dict[str, int], total: int) -> dict[str, int]:
    """Split ``total`` across bands in proportion to ``sizes`` (largest remainder)."""
    size = sum(sizes.values())
    quotas = {b: total * n / size for b, n in sizes.items()}
    take = {b: min(int(q), sizes[b]) for b, q in quotas.items()}
    for band in sorted(quotas, key=lambda b: (-(quotas[b] - take[b]), BANDS.index(b))):
        if sum(take.values()) >= total:
            break
        if take[band] < sizes[band]:
            take[band] += 1
    return take


def draw(screenings: list[Screening], seed: int = SELECTION_SEED) -> None:
    """Assign bands and groups in place (see module docstring).

    Raises:
        ValueError: if fewer than 35 problems are eligible.
    """
    eligible = sorted((s for s in screenings if s.eligible), key=lambda s: (s.mutants_after_tce, int(s.task_id.split("/")[1])))
    if len(eligible) < TOTAL_SUBJECTS:
        raise ValueError(f"only {len(eligible)} eligible problems; {TOTAL_SUBJECTS} are needed")
    for rank, s in enumerate(eligible):
        s.band = BANDS[min(2, rank * 3 // len(eligible))]
    by_band = {b: [s for s in eligible if s.band == b] for b in BANDS}
    rng = random.Random(seed)
    drawn: dict[str, list[Screening]] = {}
    for band, take in _allocate({b: len(v) for b, v in by_band.items()}, TOTAL_SUBJECTS).items():
        drawn[band] = rng.sample(by_band[band], take)
    for band, take in _allocate({b: len(v) for b, v in drawn.items()}, PILOT_SUBJECTS).items():
        for index, s in enumerate(drawn[band]):
            s.group = "pilot" if index < take else "main"


def prepare_subjects(workers: int | None = None, out_dir: Path | None = None) -> list[Screening]:
    """Screen every problem, draw the subjects and write the output files.

    Args:
        workers: Parallel screening threads (each runs subprocesses);
            default half the CPUs.
        out_dir: Subjects folder; default ``experiments/subjects``.

    Returns:
        All screening rows in task order.

    Side effects:
        Starts many sandboxed subprocesses; writes selection.csv and the 35
        subject files (other files in functions/ are removed first so the
        folder always matches selection.csv).
    """
    from testroute.mutation.runner import default_workers

    folder = out_dir or paths.SUBJECTS_DIR
    problems = load_problems()
    inputs = load_inputs()
    with ThreadPoolExecutor(max_workers=workers or default_workers()) as pool:
        screenings = list(pool.map(lambda p: screen(p, inputs.get(p.function_id, [])), problems))
    draw(screenings)

    functions_dir = folder / "functions"
    functions_dir.mkdir(parents=True, exist_ok=True)
    for old in functions_dir.glob("*.py"):
        old.unlink()
    by_id = {p.function_id: p for p in problems}
    for s in screenings:
        if s.group:
            (functions_dir / f"{s.function_id}.py").write_text(by_id[s.function_id].source, encoding="utf-8", newline="\n")
    with (folder / "selection.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for s in screenings:
            writer.writerow(s.row())
    return screenings


def read_selection(path: Path | None = None) -> list[dict[str, str]]:
    """Rows of selection.csv (used by prepare-mutants and the experiment runner)."""
    with (path or SELECTION_FILE).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def selected_subjects(group: str | None = None, path: Path | None = None) -> list[dict[str, str]]:
    """Selected rows, optionally only one group (main or pilot)."""
    return [r for r in read_selection(path) if r["group"] and (group is None or r["group"] == group)]
