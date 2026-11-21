"""Differential execution of a module on benchmark inputs.

Component: C9 (Mutation Analyzer), equivalence pipeline step 1 (spec 6.4),
also used by subject screening (spec 9.1, determinism check).

Responsibility:
    Runs one version of a module (the original or a mutant) on a list of
    inputs in a sandboxed subprocess and records, per input, either the
    encoded return value (plus the arguments after the call, so in-place
    changes count as behaviour) or the type of the raised exception (spec
    6.4: "treat a raised exception type as an output"). Outputs are compared
    with float tolerance 1e-6.

    Per-input timeout of 2 s without signals (Windows): a harness process
    evaluates inputs in order and prints one JSON line per input. The parent
    reads lines through a thread; if the next line does not arrive within
    the limit, the process tree is killed, that input is recorded as TIMEOUT
    and a new harness resumes at the next input. After ``max_timeouts``
    timeouts the remaining inputs are skipped: the mutant is already known to
    be non-equivalent, and waiting 2 s per further input would make one
    looping mutant cost minutes.

    Labels (spec 6.4 step 2), comparing a mutant with the original on every
    input where the original itself finished:
    * DIFF_OUTPUT: some output differs (confirmed non-equivalent);
    * DIFF_TIMEOUT_ONLY: no output differs but the mutant timed out where the
      original did not (non-equivalent, likely a genuine infinite loop);
    * NO_DIFF: every output equal (candidate equivalent, manual review).

Receives: module name, entry point, module source, inputs.
Produces: :class:`ExecutionTrace` and labels with evidence.
Talks to: ``sandbox`` (workspace, environment, process kill).
Spec sections: 6.4 steps 1 and 2, 9.1 (deterministic criterion).
Feedback IDs: FB2 (complete equivalence pipeline).
"""

from __future__ import annotations

import json
import math
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from testroute.sandbox import CREATE_NEW_PROCESS_GROUP, Workspace, build_sandbox_env, kill_process_tree

DIFF_OUTPUT = "DIFF_OUTPUT"
DIFF_TIMEOUT_ONLY = "DIFF_TIMEOUT_ONLY"
NO_DIFF = "NO_DIFF"

PER_INPUT_TIMEOUT_S = 2.0
# The first line also waits for interpreter start-up and the module import.
STARTUP_GRACE_S = 15.0
FLOAT_TOLERANCE = 1e-6
DEFAULT_MAX_TIMEOUTS = 3

TIMEOUT = "TIMEOUT"
SKIPPED = "SKIPPED"

# Child script. It is our own code (not generated), so it may read the input
# file. Each result line is flushed immediately so the parent can time inputs.
_HARNESS = r'''
import copy, importlib, json, math, sys
sys.path.insert(0, ".")
MODULE, ENTRY, START = sys.argv[1], sys.argv[2], int(sys.argv[3])

def enc(obj, depth=0):
    if depth > 50:
        return {"r": "<deep>"}
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj if abs(obj) < 2 ** 53 else {"bigint": str(obj)}
    if isinstance(obj, float):
        return {"f": obj if math.isfinite(obj) else repr(obj)}
    if isinstance(obj, (list, tuple)):
        return {"t": type(obj).__name__, "v": [enc(x, depth + 1) for x in obj]}
    if isinstance(obj, dict):
        items = [[enc(k, depth + 1), enc(v, depth + 1)] for k, v in obj.items()]
        return {"t": "dict", "v": sorted(items, key=repr)}
    if isinstance(obj, (set, frozenset)):
        return {"t": "set", "v": sorted((enc(x, depth + 1) for x in obj), key=repr)}
    return {"r": type(obj).__name__ + ":" + repr(obj)[:2000]}

with open("inputs.json", encoding="utf-8") as handle:
    inputs = json.load(handle)
module = importlib.import_module(MODULE)
function = getattr(module, ENTRY)
for index in range(START, len(inputs)):
    args = copy.deepcopy(inputs[index])
    try:
        value = function(*args)
        record = {"i": index, "out": enc(value), "args": enc(args)}
    except BaseException as exc:
        record = {"i": index, "exc": type(exc).__name__}
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()
'''


@dataclass
class ExecutionTrace:
    """Per-input results of one module version.

    Attributes:
        results: Index -> record. A record is {"out": ..., "args": ...},
            {"exc": TypeName}, {"status": "TIMEOUT"} or {"status": "SKIPPED"}.
        crashed: Inputs where the harness died without a line (counted as
            an exception named HarnessCrash).
        seconds: Wall-clock time of the whole execution.
    """

    results: dict[int, dict[str, Any]] = field(default_factory=dict)
    crashed: int = 0
    seconds: float = 0.0

    def timeouts(self) -> list[int]:
        """Indices that timed out."""
        return [i for i, r in self.results.items() if r.get("status") == TIMEOUT]


def execute(
    module_name: str,
    entry_point: str,
    source: str,
    inputs: list[list[Any]],
    *,
    per_input_timeout_s: float = PER_INPUT_TIMEOUT_S,
    max_timeouts: int | None = None,
    hash_seed: int = 0,
) -> ExecutionTrace:
    """Run ``entry_point`` of ``source`` on every input (see module docstring).

    Args:
        module_name: Import name used for the module file.
        entry_point: Function to call.
        source: Module source (original or mutant).
        inputs: Positional argument lists.
        per_input_timeout_s: Limit per input (spec 6.4: 2 s).
        max_timeouts: Stop after this many timeouts (None = never stop).
        hash_seed: PYTHONHASHSEED of the child; screening runs the original
            with two seeds to detect output that depends on set order.

    Returns:
        The :class:`ExecutionTrace`.

    Side effects:
        Starts sandboxed subprocesses in a temporary folder.
    """
    trace = ExecutionTrace()
    start_time = time.perf_counter()
    with Workspace(prefix="testroute_diff_") as ws:
        ws.write(f"{module_name}.py", source)
        ws.write("inputs.json", json.dumps(inputs))
        env = build_sandbox_env(ws.path)
        env["PYTHONHASHSEED"] = str(hash_seed)
        next_index = 0
        while next_index < len(inputs):
            next_index = _run_from(ws.path, env, module_name, entry_point, next_index, len(inputs),
                                   per_input_timeout_s, trace)
            if max_timeouts is not None and len(trace.timeouts()) >= max_timeouts:
                for index in range(next_index, len(inputs)):
                    trace.results[index] = {"status": SKIPPED}
                break
    trace.seconds = time.perf_counter() - start_time
    return trace


def _run_from(workdir, env, module_name, entry_point, start, total, limit, trace: ExecutionTrace) -> int:
    """Run one harness process from input ``start``; return the next index to run."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _HARNESS, module_name, entry_point, str(start)],
        cwd=str(workdir), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            lines.put(line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    expected = start
    wait = STARTUP_GRACE_S + limit
    while expected < total:
        try:
            line = lines.get(timeout=wait)
        except queue.Empty:
            # No result within the limit: the current input hangs.
            kill_process_tree(proc.pid)
            proc.wait()
            trace.results[expected] = {"status": TIMEOUT}
            return expected + 1
        if line is None:
            # The harness ended early: the module failed to import or the
            # interpreter crashed on this input (for example a C stack
            # overflow). Recorded as an exception so it still compares.
            proc.wait()
            trace.results[expected] = {"exc": "HarnessCrash"}
            trace.crashed += 1
            return expected + 1
        record = json.loads(line)
        index = record.pop("i")
        trace.results[index] = record
        expected = index + 1
        wait = limit
    proc.wait()
    return expected


def same_output(a: Any, b: Any) -> bool:
    """Compare two encoded values; floats within tolerance 1e-6 (spec 6.4)."""
    if isinstance(a, dict) and isinstance(b, dict) and "f" in a and "f" in b and len(a) == len(b) == 1:
        x, y = a["f"], b["f"]
        if isinstance(x, str) or isinstance(y, str):
            return x == y
        return math.isclose(x, y, rel_tol=FLOAT_TOLERANCE, abs_tol=FLOAT_TOLERANCE)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_output(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_output(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def same_record(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two per-input records describe the same behaviour."""
    if "exc" in a or "exc" in b:
        return a.get("exc") == b.get("exc")
    return same_output(a.get("out"), b.get("out")) and same_output(a.get("args"), b.get("args"))


@dataclass
class Comparison:
    """Differential verdict for one mutant, with the evidence for review."""

    label: str
    inputs_compared: int
    first_difference: int | None = None
    timeouts: int = 0
    skipped: int = 0
    equal_exceptions: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form stored in the differential results file."""
        return dict(self.__dict__)


def compare(original: ExecutionTrace, mutant: ExecutionTrace) -> Comparison:
    """Label a mutant from its trace and the original's (spec 6.4 step 2).

    Inputs on which the original itself timed out are not compared.
    """
    compared = equal_exc = 0
    first_diff: int | None = None
    timeout_diff = 0
    for index, orig in original.results.items():
        if orig.get("status") in (TIMEOUT, SKIPPED):
            continue
        result = mutant.results.get(index, {"status": SKIPPED})
        status = result.get("status")
        if status == SKIPPED:
            continue
        compared += 1
        if status == TIMEOUT:
            timeout_diff += 1
            continue
        if not same_record(orig, result):
            first_diff = index if first_diff is None else first_diff
        elif "exc" in orig:
            equal_exc += 1
    if first_diff is not None:
        label = DIFF_OUTPUT
    elif timeout_diff:
        label = DIFF_TIMEOUT_ONLY
    else:
        label = NO_DIFF
    skipped = sum(1 for r in mutant.results.values() if r.get("status") == SKIPPED)
    return Comparison(label, compared, first_diff, timeout_diff, skipped, equal_exc, round(mutant.seconds, 3))


def usable_inputs(original: ExecutionTrace) -> int:
    """Inputs on which the original finished (the comparison base)."""
    return sum(1 for r in original.results.values() if r.get("status") not in (TIMEOUT, SKIPPED))
