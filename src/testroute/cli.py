"""Command-line entry point: ``python -m testroute <command>``.

Component: new module ``cli.py`` (spec Section 5).

Responsibility:
    Parses the command line and dispatches to the component that implements
    each command. The command set is fixed by spec Section 5:
    ``run``, ``prepare-subjects``, ``prepare-mutants``, ``review-equivalence``,
    ``experiment``, ``merge``, ``analyze``, ``check-env``, ``export-trace``.

    The build follows the milestones of spec Section 15. A command whose
    milestone is not built yet is still registered, so ``--help`` shows the
    full interface, but it prints which milestone delivers it and exits with
    code 2 instead of doing partial work.

Receives: ``argv`` (a list of strings, defaulting to ``sys.argv[1:]``).
Produces: an integer exit code that ``__main__`` and the ``.bat`` launchers
    pass on (0 success, 1 failure, 2 not yet available or bad usage).
Talks to: ``check_env`` (milestone 1), ``trace`` (milestone 2),
    ``single_run`` (milestone 4); C1 and C12 as they are built.
Spec sections: 5, 12, 15.
Feedback IDs: FB9 (one automated entry point for every step).
"""

from __future__ import annotations

import argparse
import sys

from testroute import __version__

# Milestone (spec Section 15) that delivers each command not built yet.
# Entries are removed from this table as each milestone lands.
PENDING_COMMANDS: dict[str, tuple[int, str]] = {
    "experiment": (6, "Run a planned experiment (main, pilot or demo)."),
    "merge": (6, "Merge results folders, deduplicating by run id."),
    "analyze": (7, "Run the statistical analysis on a results folder."),
}


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser with every spec Section 5 command.

    Returns:
        The configured ``argparse.ArgumentParser``.

    Raises:
        Nothing.

    Side effects:
        None.
    """
    parser = argparse.ArgumentParser(
        prog="python -m testroute",
        description="TestRoute: LLM unit test generation with mutation feedback and routing.",
    )
    parser.add_argument("--version", action="version", version=f"testroute {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    check = sub.add_parser("check-env", help="Check Python, packages, configs, .env and Ollama.")
    check.add_argument(
        "--setup",
        action="store_true",
        help="Setup mode used by setup.bat: an empty API key is only a warning.",
    )

    run = sub.add_parser("run", help="Run one function under one condition and write a full trace (run.bat).")
    run.add_argument("file", nargs="?", default=None, help="Python file (default examples/input/sample_input.py).")
    run.add_argument("function", nargs="?", default=None, help="Function under test (default: the only public function).")
    run.add_argument("condition", nargs="?", default=None, help="Condition such as C-F4, L-F4 or ESC (default C-F4).")
    run.add_argument("--trial", type=int, default=None, help="Trial number (default 1); local calls use seed 1000 x trial + call number.")
    run.add_argument("--quiet", action="store_true", help="Do not print run.log lines while running.")

    subjects = sub.add_parser("prepare-subjects", help="Screen all 164 HumanEval problems and draw the 35 subjects.")
    subjects.add_argument("--workers", type=int, default=None, help="Parallel screening workers (default cpu_count // 2).")

    mutants = sub.add_parser(
        "prepare-mutants",
        help="Generate mutants and run differential execution; with --finalize, apply the manual review.",
    )
    mutants.add_argument("--workers", type=int, default=None, help="Parallel mutant executions (default cpu_count // 2).")
    mutants.add_argument("--functions", nargs="*", default=None, help="Only these function ids, for example HumanEval_12.")
    mutants.add_argument(
        "--finalize",
        action="store_true",
        help="After the manual review: apply labels, cap and split, write experiments/mutants/*.json.",
    )

    sub.add_parser("review-equivalence", help="Write the manual equivalence review files (labels stay empty).")

    export = sub.add_parser("export-trace", help="Export the component-by-component trace of one run.")
    export.add_argument("--run-id", required=True, help="Run id, for example HumanEval_12__C-F4__t1.")
    export.add_argument("--results-dir", default="results/main", help="Results folder of the run (default results/main).")
    export.add_argument("--to", default=None, help="Destination folder (default <results-dir>/traces/<run-id>).")
    export.add_argument(
        "--to-examples",
        action="store_true",
        help="Also copy the trace onto the required examples/ file names (needs 2 or more attempts).",
    )

    for name, (milestone, summary) in PENDING_COMMANDS.items():
        pending = sub.add_parser(name, help=f"{summary} (available from milestone {milestone})")
        # Accept and ignore any options so a launcher calling a pending command
        # gets the clear "not built yet" message rather than a usage error.
        pending.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the chosen command.

    Args:
        argv: Arguments without the program name. ``None`` means
            ``sys.argv[1:]``.

    Returns:
        The command's exit code: 0 success, 1 failure, 2 for bad usage or a
        command whose milestone is not built yet.

    Raises:
        SystemExit: only from argparse for ``--help``, ``--version`` or a
            usage error, as argparse always does.

    Side effects:
        Whatever the dispatched command does (see its module docstring).
    """
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "check-env":
        # Imported lazily so "--help" works even when a dependency is missing.
        # check_env needs PyYAML and python-dotenv just to load; if either is
        # absent, report that plainly instead of showing a traceback.
        try:
            from testroute.check_env import run_check_env
        except ImportError as exc:
            print(f"check-env: FAILED (required package missing: {exc.name}; run setup.bat)")
            return 1

        return run_check_env(setup_mode=args.setup)

    if args.command == "export-trace":
        return _export_trace(args)

    if args.command in ("prepare-subjects", "prepare-mutants", "review-equivalence"):
        return _prepare(args)

    if args.command == "run":
        from testroute.single_run import run_single

        return run_single(args.file, args.function, args.condition, trial=args.trial, echo=not args.quiet)

    milestone, _summary = PENDING_COMMANDS[args.command]
    print(f"'{args.command}' is not available yet: it is delivered in build milestone {milestone} (spec Section 15).")
    return 2


def _export_trace(args: argparse.Namespace) -> int:
    """Handle ``export-trace``: copy a run's trace and optionally fill examples/.

    Returns:
        0 on success, 1 when the run or its trace does not exist or has too
        few attempts for ``--to-examples``.

    Side effects:
        Writes the exported trace folder, and with ``--to-examples`` files
        under ``examples/``.
    """
    from pathlib import Path

    from testroute import paths
    from testroute.trace import export_to_examples, export_trace

    results_dir = Path(args.results_dir)
    if not results_dir.is_absolute():
        results_dir = paths.PROJECT_ROOT / results_dir
    dest = Path(args.to) if args.to else results_dir / "traces" / args.run_id
    try:
        out = export_trace(results_dir, args.run_id, dest)
        print(f"Trace of {args.run_id} written to {out}")
        if args.to_examples:
            written = export_to_examples(results_dir, args.run_id)
            print(f"{len(written)} example files written under {paths.EXAMPLES_DIR}")
    except (FileNotFoundError, ValueError) as exc:
        print(f"export-trace: FAILED ({exc})")
        return 1
    return 0


def _prepare(args: argparse.Namespace) -> int:
    """Handle the experiment preparation commands of milestone 5 (spec 6.4, 9.1).

    Returns:
        0 on success; 1 when a step cannot run (for example the review is
        incomplete or an earlier step was not run).

    Side effects:
        See ``subject_selection.prepare_subjects`` and the ``equivalence``
        module.
    """
    from collections import Counter

    from testroute import equivalence, paths, subject_selection

    try:
        if args.command == "prepare-subjects":
            rows = subject_selection.prepare_subjects(workers=args.workers)
            eligible = [r for r in rows if r.eligible]
            failed = Counter(r.failed_criterion for r in rows if not r.eligible)
            print(f"Screened {len(rows)} problems: {len(eligible)} eligible.")
            for criterion, count in failed.most_common():
                print(f"  failed {criterion}: {count}")
            groups = Counter((r.group, r.band) for r in rows if r.group)
            print("Selected:", ", ".join(f"{g} {b} {n}" for (g, b), n in sorted(groups.items())))
            print(f"Wrote {subject_selection.SELECTION_FILE} and {paths.SUBJECT_FUNCTIONS_DIR}")
        elif args.command == "prepare-mutants" and args.finalize:
            summaries = equivalence.finalize_mutants()
            print(f"Final mutant files written for {len(summaries)} subjects in {paths.MUTANTS_DIR}")
        elif args.command == "prepare-mutants":
            summaries = equivalence.prepare_mutants(workers=args.workers, function_ids=args.functions)
            totals = Counter()
            for s in summaries:
                totals.update({k: v for k, v in s.items() if isinstance(v, int)})
            print(f"Differential execution done for {len(summaries)} subjects: " + ", ".join(
                f"{k} {totals[k]}" for k in ("generated", "TCE_EQUIVALENT", "DUPLICATE", "DIFF_OUTPUT", "DIFF_TIMEOUT_ONLY", "NO_DIFF")))
            print("Next: python -m testroute review-equivalence")
        else:
            result = equivalence.review_equivalence()
            print(f"Review files written for {result.functions_with_candidates} functions; "
                  f"{result.rows_total} rows in {equivalence.REVIEW_CSV} ({result.rows_added} new).")
            print("Labels are empty: both reviewers fill reviewer_1 and reviewer_2 by hand, then final_label and")
            print("justification; afterwards run: python -m testroute prepare-mutants --finalize")
    except (FileNotFoundError, ValueError, equivalence.ReviewIncomplete) as exc:
        print(f"{args.command}: FAILED")
        print(exc)
        return 1
    return 0
