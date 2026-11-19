"""Bundled benchmark data: HumanEval problems and HumanEval+ inputs.

Component: support module for subject screening (spec Section 9.1) and the
equivalence pipeline (spec Section 6.4); not a row of the Section 5 table.

Responsibility:
    Reads the two files committed under ``experiments/subjects/`` so the grader
    needs no network for data (spec Section 9.1):
    * ``HumanEval.jsonl.gz``: the 164 HumanEval problems (MIT licence),
      unchanged from github.com/openai/human-eval;
    * ``humaneval_plus_inputs.jsonl.gz``: per problem, the inputs of
      HumanEval's own tests (``base_input``) and a seeded sample of at most
      200 HumanEval+ inputs (``plus_input``), extracted from the EvalPlus
      release (Apache-2.0 licence) by ``tools/fetch_datasets.py``.
    These inputs are used only for subject screening and differential
    execution. They are never shown to a model and never used to score
    generated tests (spec Section 6.4).

Receives: nothing (reads the bundled files).
Produces: :class:`Problem` objects and input lists.
Talks to: used by ``subject_selection`` and ``equivalence``.
Spec sections: 6.4, 9.1.
Feedback IDs: FB2 (inputs for differential execution), FB8 (benchmark).
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from testroute import paths

HUMANEVAL_FILE: Path = paths.SUBJECTS_DIR / "HumanEval.jsonl.gz"
PLUS_INPUTS_FILE: Path = paths.SUBJECTS_DIR / "humaneval_plus_inputs.jsonl.gz"


@dataclass(frozen=True)
class Problem:
    """One HumanEval problem.

    Attributes:
        task_id: For example ``HumanEval/12``.
        function_id: File-safe id, for example ``HumanEval_12``; also the
            module name of the subject file.
        entry_point: Function under test.
        source: Module source: the prompt (signature and docstring, plus any
            helper functions or imports) followed by the canonical solution.
        test: HumanEval's own test code, defining ``check(candidate)``.
    """

    task_id: str
    function_id: str
    entry_point: str
    source: str
    test: str


def function_id_for(task_id: str) -> str:
    """``HumanEval/12`` -> ``HumanEval_12``."""
    return task_id.replace("/", "_")


def _read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"bundled data file missing: {path}; run tools\\fetch_datasets.py once")
    text = gzip.decompress(path.read_bytes()).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_problems(path: Path | None = None) -> list[Problem]:
    """Load all HumanEval problems in task order.

    Raises:
        FileNotFoundError: if the bundled file is missing.
    """
    problems = []
    for row in _read_jsonl_gz(path or HUMANEVAL_FILE):
        source = row["prompt"] + row["canonical_solution"]
        source = source.replace("\r\n", "\n").rstrip("\n") + "\n"
        problems.append(
            Problem(row["task_id"], function_id_for(row["task_id"]), row["entry_point"], source, row["test"])
        )
    return sorted(problems, key=lambda p: int(p.task_id.split("/")[1]))


def load_inputs(path: Path | None = None) -> dict[str, list[list[Any]]]:
    """Inputs per function id: HumanEval's own test inputs first, then HumanEval+.

    Each input is the list of positional arguments of one call.

    Raises:
        FileNotFoundError: if the bundled file is missing.
    """
    inputs: dict[str, list[list[Any]]] = {}
    for row in _read_jsonl_gz(path or PLUS_INPUTS_FILE):
        inputs[function_id_for(row["task_id"])] = list(row["base_input"]) + list(row["plus_input"])
    return inputs
