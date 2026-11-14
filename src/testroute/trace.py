"""Behind-the-scenes trace of one run, and its export.

Component: new module ``trace.py`` (spec Section 5 and Section 13).

Responsibility:
    * :class:`TraceWriter` is used DURING a run: each component's input or
      output is written to ``<results>/artifacts/<run_id>/trace/`` under the
      numbered names of spec Section 13. Attempt 1 uses the plain names
      (``03_prompt_sent.txt``); later attempts are prefixed
      (``attempt2_03_prompt_sent.txt``). Run-level files (00, 01, 20, 21) have
      no prefix.
    * :func:`export_trace` (command ``export-trace``) copies the trace of a run
      into a destination folder, adds ``run.log``, the final tests, the run
      record and a generated ``TRACE_README.md`` that explains every file in
      one line, so a reader can follow the data with no source code.
    * :func:`export_to_examples` (``export-trace --to-examples``) maps a trace
      with at least two attempts onto the file names the D3 submission
      requires under ``examples/``.

Receives: data from the controller (C10) and the records store (C11) during
    a run; a results folder and run id for export.
Produces: trace files; an exported trace folder; files in ``examples/``.
Talks to: written to by C10 and C11; reads ``runs.jsonl`` for export.
Spec sections: 13.
Feedback IDs: FB9 (shows that the system runs end to end automatically).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from testroute import paths
from testroute.logging_setup import redact

# One-line descriptions used to generate TRACE_README.md. Keys are the file
# names of attempt 1; later attempts reuse the description of the same number.
TRACE_FILES: dict[str, str] = {
    "00_input_function.py": "The function under test exactly as the system received it.",
    "01_C2_subject.json": "C2 Subject Loader output: module name, entry point, import time, line count.",
    "02_C3_to_C4_prompt_request.json": "C3 Prompt Builder output passed to C4: template id and hash, prompt hash, size estimate, shortening steps, chosen role.",
    "03_prompt_sent.txt": "The exact system and user message sent to the model.",
    "04_C5_raw_response.txt": "The complete text returned by the model.",
    "05_C5_to_C6_llm_result.json": "C5 LLM Client result: provider, model, fingerprint, tokens, latency, finish reason, retries.",
    "06_C6_parsed_tests.py": "C6 Response Parser output: the test file extracted from the response.",
    "06_C6_parse_report.json": "C6 parse report: which code block was taken and what was stripped.",
    "07_C7_static_validation.json": "C7 Static Validator result (checks V2 and V3) with every violation found.",
    "08_C8_test_results.json": "C8 test run (V4, V5): outcome and message of every test, and the filtered suite.",
    "08_C8_coverage.json": "C8 line and branch coverage of the filtered suite, with uncovered lines and branches.",
    "09_C9_mutation_feedback_view.json": "C9 feedback-set mutant results: the only mutation data C10 can see.",
    "09_C9_mutation_full_results.json": "C9 results of all mutants including held-out ones, sent straight to C11 (never to C10).",
    "10_C10_decision.json": "C10 decision: best suite so far, quality target check, next action.",
    "11_C10_to_C3_feedback_bundle.json": "C10 feedback bundle handed to C3 for the next prompt.",
    "20_final_tests.py": "Final test suite after removing failing and flaky tests.",
    "21_run_record.json": "The run record written to runs.jsonl (all metrics, cost and time).",
    "run.log": "Step-by-step log, one line per step: [HH:MM:SS] <component> <message>.",
}


def trace_file_name(base: str, attempt_no: int | None) -> str:
    """Name of a trace file for an attempt (None = run-level file)."""
    if attempt_no is None or attempt_no == 1:
        return base
    return f"attempt{attempt_no}_{base}"


class TraceWriter:
    """Writes trace files for one run during execution."""

    def __init__(self, trace_dir: Path) -> None:
        """Args: trace_dir: ``<results>/artifacts/<run_id>/trace``. Created now."""
        self.dir = trace_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def text(self, base: str, content: str, attempt_no: int | None = None) -> Path:
        """Write a text trace file (redacted) and return its path."""
        path = self.dir / trace_file_name(base, attempt_no)
        path.write_text(redact(content), encoding="utf-8", newline="\n")
        return path

    def json(self, base: str, data: Any, attempt_no: int | None = None) -> Path:
        """Write a JSON trace file (redacted, indented) and return its path."""
        return self.text(base, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", attempt_no)


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file; a missing file is an empty list."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _describe(name: str) -> str:
    """Description for TRACE_README.md, matching attempt-prefixed names too."""
    if name in TRACE_FILES:
        return TRACE_FILES[name]
    if name.startswith("attempt") and "_" in name:
        prefix, base = name.split("_", 1)
        if base in TRACE_FILES:
            return f"Same as {base}, for {prefix.replace('attempt', 'attempt ')}."
    return "Additional file."


def export_trace(results_dir: Path, run_id: str, dest: Path) -> Path:
    """Copy the complete trace of one run into ``dest``.

    Args:
        results_dir: Results folder that holds ``runs.jsonl`` and
            ``artifacts/<run_id>/``.
        run_id: Run to export.
        dest: Destination folder (created; existing files are overwritten).

    Returns:
        ``dest``.

    Raises:
        FileNotFoundError: when the run has no trace folder.

    Side effects:
        Creates ``dest`` and writes files into it.
    """
    artifacts = results_dir / "artifacts" / run_id
    trace_dir = artifacts / "trace"
    if not trace_dir.is_dir():
        raise FileNotFoundError(f"no trace for run {run_id} in {results_dir}")
    dest.mkdir(parents=True, exist_ok=True)
    for item in trace_dir.iterdir():
        shutil.copy2(item, dest / item.name)
    if (artifacts / "run.log").is_file():
        shutil.copy2(artifacts / "run.log", dest / "run.log")
    if (artifacts / "final_tests.py").is_file():
        shutil.copy2(artifacts / "final_tests.py", dest / "20_final_tests.py")
    records = [r for r in read_jsonl(results_dir / "runs.jsonl") if r.get("run_id") == run_id]
    if records:
        (dest / "21_run_record.json").write_text(
            json.dumps(records[-1], indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
    names = sorted(p.name for p in dest.iterdir() if p.name != "TRACE_README.md")
    lines = [
        f"# Trace of run {run_id}",
        "",
        "Files are numbered in the order data flows through the components (C1 to C12).",
        "Files of attempt 2 and later carry an attemptN_ prefix.",
        "",
        "| File | What it shows |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | {_describe(name)} |" for name in names]
    (dest / "TRACE_README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return dest


def export_to_examples(results_dir: Path, run_id: str, examples_dir: Path | None = None) -> list[Path]:
    """Map a run's trace onto the D3 ``examples/`` file names (spec Section 13).

    Args:
        results_dir: Results folder of the run.
        run_id: A run with at least two attempts (ideally an escalated ESC run).
        examples_dir: Destination root; default ``<root>/examples``.

    Returns:
        Paths of the files written.

    Raises:
        FileNotFoundError: when the run has no trace.
        ValueError: when the run has fewer than two attempts, because the
            examples must show a feedback prompt and its response.

    Side effects:
        Overwrites files under ``examples/``.
    """
    root = examples_dir if examples_dir is not None else paths.EXAMPLES_DIR
    trace_dir = results_dir / "artifacts" / run_id / "trace"
    if not trace_dir.is_dir():
        raise FileNotFoundError(f"no trace for run {run_id} in {results_dir}")
    if not (trace_dir / "attempt2_03_prompt_sent.txt").is_file():
        raise ValueError(f"run {run_id} has a single attempt; choose a run with feedback (2 or more attempts)")

    full = export_trace(results_dir, run_id, results_dir / "traces" / run_id)
    mapping = {
        "00_input_function.py": "input/sample_input.py",
        "03_prompt_sent.txt": "prompts/sample_prompt.txt",
        "attempt2_03_prompt_sent.txt": "prompts/sample_feedback_prompt.txt",
        "04_C5_raw_response.txt": "llm_responses/sample_llm_response.txt",
        "attempt2_04_C5_raw_response.txt": "llm_responses/sample_feedback_llm_response.txt",
        "02_C3_to_C4_prompt_request.json": "intermediate/module_A_to_B.json",
        "run.log": "logs/sample_run.log",
        "20_final_tests.py": "output/generated_tests.py",
        "21_run_record.json": "output/sample_output.json",
    }
    written: list[Path] = []
    for source_name, target in mapping.items():
        source = full / source_name
        if source.is_file():
            out = root / target
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, out)
            written.append(out)
    intermediate = root / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    index = [
        f"Intermediate files copied from the trace of run {run_id}.",
        "module_A_to_B.json is the C3 to C4 prompt request (component A = Prompt Builder, B = Router).",
        "",
    ]
    for item in sorted(full.glob("*.json")):
        shutil.copy2(item, intermediate / item.name)
        written.append(intermediate / item.name)
        index.append(f"{item.name}: {_describe(item.name)}")
    (intermediate / "README.txt").write_text("\n".join(index) + "\n", encoding="utf-8", newline="\n")
    written.append(intermediate / "README.txt")
    return written
