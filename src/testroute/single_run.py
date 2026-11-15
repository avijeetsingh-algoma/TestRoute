"""One ad hoc run on any Python file: the ``run`` command behind run.bat.

Component: support module for ``cli.py`` (command ``run``); it wires C2, C4,
C5, C9, C10, C11 and the trace exporter for a single run.

Responsibility:
    ``run.bat [path_to_py] [function_name] [condition]`` (spec Section 12,
    FB9) takes any Python file plus a function name and:
    1. loads and validates the subject (C2);
    2. builds its mutant set on the fly (C9: generation, TCE, duplicates,
       cap, split). An ad hoc file has no HumanEval inputs, so the
       equivalence pipeline of spec 6.4 cannot run; its mutants are labelled
       NOT_REVIEWED and the run folder says so;
    3. creates the providers (C5); if the condition needs Ollama and it is
       unreachable, it stops with a message suggesting a cloud condition;
    4. executes the run (C10) into ``results/sample/<timestamp>/``;
    5. exports the full trace (spec Section 13) to ``<folder>/trace/`` and
       prints where everything is.

Receives: file path, function name, condition, trial, options.
Produces: a results folder with records, artifacts, mutants.json and trace;
    printed summary; an exit code.
Talks to: subjects (C2), mutation (C9), llm.factory (C5), controller (C10),
    records (C11), trace.
Spec sections: 12 (run.bat), 13 (trace).
Feedback IDs: FB9 (any file plus function name, fully automated).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from testroute import paths
from testroute.controller import RunController
from testroute.llm.factory import ProviderUnavailable, build_providers
from testroute.logging_setup import redact
from testroute.mutation.mutant_set import build_mutant_set
from testroute.mutation.runner import MutantRunner
from testroute.records import STATUS_COMPLETE, ResultsStore
from testroute.router import ConditionError, parse_condition
from testroute.settings import load_config, load_env_settings
from testroute.subjects import SubjectError, load_subject
from testroute.trace import export_trace

# Exit codes of the run command (passed on by run.bat).
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_BAD_INPUT = 2
EXIT_PROVIDER_UNAVAILABLE = 3


def run_single(
    file_path: str | None,
    function_name: str | None,
    condition_name: str | None,
    *,
    trial: int | None = None,
    results_root: Path | None = None,
    echo: bool = True,
) -> int:
    """Execute one run and export its trace.

    Args:
        file_path: Python file; default from config/sample_run.yaml.
        function_name: Function under test; default: the only public
            function of the file.
        condition_name: Condition; default from config/sample_run.yaml (C-F4).
        trial: Trial number (local seeds 1000 x trial + call number); default from config.
        results_root: Parent folder for the timestamped run folder; default
            ``results/sample``.
        echo: Print run.log lines while running.

    Returns:
        EXIT_OK when the run completed; EXIT_RUN_FAILED for INFRA or TOOL
        failures; EXIT_BAD_INPUT for an unusable file, function or condition;
        EXIT_PROVIDER_UNAVAILABLE when a needed model cannot be reached.

    Side effects:
        Network calls to the configured models; subprocesses; files under the
        results folder.
    """
    config = load_config("sample_run.yaml")
    models = load_config("models.yaml")
    path = Path(file_path or config["input_file"])
    if not path.is_absolute():
        path = (Path.cwd() / path) if (Path.cwd() / path).is_file() else paths.PROJECT_ROOT / path
    try:
        condition = parse_condition(condition_name or config["condition"], models.get("call_budget"))
        subject = load_subject(path, function_name or config.get("function_name") or None)
    except (ConditionError, SubjectError) as exc:
        print(f"run: {exc}")
        return EXIT_BAD_INPUT

    try:
        providers = build_providers(condition, models, load_env_settings())
    except ProviderUnavailable as exc:
        print(f"run: the {exc.role} model is not available for condition {condition.name}: {redact(str(exc))}")
        return EXIT_PROVIDER_UNAVAILABLE

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (results_root or paths.PROJECT_ROOT / config["results_dir"]) / stamp
    store = ResultsStore(out_dir)
    mutation_cfg = config["mutation"]
    mutant_set = build_mutant_set(
        subject.function_id, subject.module_name, subject.entry_point, subject.source,
        cap=int(mutation_cfg["mutant_cap"]), cap_seed=int(mutation_cfg["cap_seed"]),
        split_seed=int(mutation_cfg["split_seed"]), prompt_seed=int(mutation_cfg["prompt_mutant_seed"]),
    )
    mutant_set.save(out_dir / "mutants.json")
    counts = mutant_set.stage_counts()
    print(
        f"Subject {subject.module_name}.{subject.entry_point}: {counts['generated']} mutants generated, "
        f"{counts['scoring']} scoring ({counts['feedback']} feedback, {counts['heldout']} held-out). "
        "Ad hoc input: equivalence review not available, mutants are NOT_REVIEWED."
    )

    record = RunController(
        subject=subject,
        condition=condition,
        trial=int(trial or config.get("trial", 1)),
        providers=providers,
        analyzer=MutantRunner.from_config(mutant_set, store, mutation_cfg),
        store=store,
        config=config,
        echo=echo,
    ).execute()
    trace_dir = export_trace(out_dir, record["run_id"], out_dir / "trace")

    print()
    print(f"Run {record['run_id']}: status {record['status']}")
    if record["status"] == STATUS_COMPLETE:
        print(
            f"  calls {record['calls_total']} (local {record['calls_local']}, cloud {record['calls_cloud']}), "
            f"escalated {record['escalated']}, target met {record['target_met']}"
        )
        print(
            f"  final suite: {record['tests_valid']} tests, line {record['line_coverage']}%, "
            f"branch {record['branch_coverage']}%, held-out strict MS {record['heldout_strict_ms']}"
        )
        print(f"  API cost (list) {record['api_cost_list_usd']:.6f} USD, total time {record['total_time_s']:.1f} s")
    else:
        print(f"  reason: {record['error']}")
    print(f"Results folder: {out_dir}")
    print(f"Trace (start with TRACE_README.md): {trace_dir}")
    print(f"Final tests: {out_dir / record['final_tests_path']}")
    return EXIT_OK if record["status"] == STATUS_COMPLETE else EXIT_RUN_FAILED
