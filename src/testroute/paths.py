"""Project folder locations, all relative to the project root.

Component: support module shared by every component (not a row of the spec
Section 5 table; it exists so no module hard-codes a path).

Responsibility:
    Finds the project root from this file's own location and defines every
    folder and well-known file the pipeline reads or writes, as ``pathlib``
    paths. Spec rule 1.1 forbids paths tied to a student's machine, so every
    other module builds its paths from the constants here.

Receives: nothing (the root is derived from ``__file__``).
Produces: ``Path`` constants and :func:`required_output_dirs`.
Talks to: imported by ``settings``, ``check_env`` and later components.
Spec sections: 1.1 (relative pathlib paths), 10 (data recorded),
12 (repository layout).
Feedback IDs: FB9 (runs anywhere without manual folder setup).
"""

from pathlib import Path

# This file is src/testroute/paths.py, so the project root is three levels up
# (paths.py -> testroute -> src -> root). This holds for the editable install
# done by setup.bat, which runs the package straight from the source tree.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# Top-level inputs.
CONFIG_DIR: Path = PROJECT_ROOT / "config"
PROMPTS_DIR: Path = PROJECT_ROOT / "prompts"
ENV_FILE: Path = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE: Path = PROJECT_ROOT / ".env.example"
REQUIREMENTS_FILE: Path = PROJECT_ROOT / "requirements.txt"

# Experiment inputs prepared once before the pilot (spec Sections 6.4 and 9.1).
EXPERIMENTS_DIR: Path = PROJECT_ROOT / "experiments"
SUBJECTS_DIR: Path = EXPERIMENTS_DIR / "subjects"
SUBJECT_FUNCTIONS_DIR: Path = SUBJECTS_DIR / "functions"
MUTANTS_DIR: Path = EXPERIMENTS_DIR / "mutants"
EQUIVALENCE_DIR: Path = EXPERIMENTS_DIR / "equivalence"

# Outputs (spec Section 10). Each experiment writes into its own folder.
RESULTS_DIR: Path = PROJECT_ROOT / "results"
RESULTS_MAIN_DIR: Path = RESULTS_DIR / "main"
RESULTS_PILOT_DIR: Path = RESULTS_DIR / "pilot"
RESULTS_DEMO_DIR: Path = RESULTS_DIR / "demo"
RESULTS_SAMPLE_DIR: Path = RESULTS_DIR / "sample"

# Required example folders for the D3 submission (spec Section 13).
EXAMPLES_DIR: Path = PROJECT_ROOT / "examples"
EXAMPLE_SUBDIRS: tuple[str, ...] = (
    "input",
    "prompts",
    "llm_responses",
    "intermediate",
    "logs",
    "output",
)


def required_output_dirs() -> list[Path]:
    """List every folder that must exist before the pipeline runs.

    setup.bat creates the same folders with ``mkdir``; ``check-env`` calls this
    function to confirm none is missing, so the two lists cannot silently
    drift apart without check-env reporting it.

    Args:
        None.

    Returns:
        Absolute ``Path`` objects in a stable order.

    Raises:
        Nothing.

    Side effects:
        None. The folders are only listed, not created.
    """
    dirs = [
        SUBJECT_FUNCTIONS_DIR,
        MUTANTS_DIR,
        EQUIVALENCE_DIR,
        RESULTS_MAIN_DIR,
        RESULTS_PILOT_DIR,
        RESULTS_DEMO_DIR,
        RESULTS_SAMPLE_DIR,
    ]
    dirs.extend(EXAMPLES_DIR / name for name in EXAMPLE_SUBDIRS)
    return dirs
