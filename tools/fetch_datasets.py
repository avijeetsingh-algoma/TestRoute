"""One-time download of the benchmark data bundled in experiments/subjects/.

Component: maintenance script (not part of the pipeline; the grader never
needs to run it, because its outputs are committed). Spec Section 9.1:
"downloaded once by a script and committed".

What it does:
    1. Downloads HumanEval.jsonl.gz from github.com/openai/human-eval
       (MIT licence) and HumanEvalPlus.jsonl.gz v0.1.10 from the EvalPlus
       release on GitHub (Apache-2.0 licence) into a new temporary folder.
    2. Checks each download against the SHA-256 recorded below and stops if
       either differs, so the bundled data is exactly the data we screened.
    3. Copies HumanEval.jsonl.gz unchanged to experiments/subjects/.
    4. Writes experiments/subjects/humaneval_plus_inputs.jsonl.gz holding,
       per problem, only the inputs: base_input (the inputs of HumanEval's
       own tests) and a seeded sample (seed 20261005) of at most 200
       plus_input entries, as spec Section 6.4 step 1 allows ("up to 200
       inputs per function from HumanEval+"). The EvalPlus reference
       solutions and expected outputs are not kept.
    The downloaded files are treated as data: they are parsed as JSON and
    never executed by this script.

Usage (from the project root, after setup.bat):
    .venv\\Scripts\\python.exe tools\\fetch_datasets.py

Inputs: network access to github.com.
Outputs: the two files above. Exit code 0 on success, 1 on a download or
    checksum failure.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "experiments" / "subjects"

SOURCES = {
    "HumanEval.jsonl.gz": (
        "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz",
        "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef",
    ),
    "HumanEvalPlus.jsonl.gz": (
        "https://github.com/evalplus/humanevalplus_release/releases/download/v0.1.10/HumanEvalPlus.jsonl.gz",
        "272720b90ac375502c8ed23cd791c2a93dfb22a911641a494da74a426c09f101",
    ),
}

# Spec 6.4 step 1: up to 200 HumanEval+ inputs per function.
MAX_PLUS_INPUTS = 200
SAMPLE_SEED = 20261005


def download(url: str, target: Path, sha256: str) -> None:
    """Download ``url`` to ``target`` and verify its SHA-256.

    Raises:
        ValueError: on a checksum mismatch.
        OSError: on a network failure.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "testroute-dataset-fetch"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != sha256:
        raise ValueError(f"{target.name}: SHA-256 {digest} does not match the recorded {sha256}")


def trim_plus(source: Path, target: Path) -> int:
    """Keep only the inputs of HumanEval+ (see module docstring). Returns rows written."""
    rows = [json.loads(line) for line in gzip.decompress(source.read_bytes()).decode("utf-8").splitlines() if line]
    out_lines = []
    for row in rows:
        plus = list(row["plus_input"])
        if len(plus) > MAX_PLUS_INPUTS:
            # One generator per task, seeded from the task number, so the
            # sample of one task does not depend on the others.
            rng = random.Random(SAMPLE_SEED + int(row["task_id"].split("/")[1]))
            keep = sorted(rng.sample(range(len(plus)), MAX_PLUS_INPUTS))
            plus = [plus[i] for i in keep]
        out_lines.append(
            json.dumps(
                {
                    "task_id": row["task_id"],
                    "entry_point": row["entry_point"],
                    "atol": row.get("atol", 0),
                    "base_input": row["base_input"],
                    "plus_input": plus,
                }
            )
        )
    # mtime=0 makes the gzip bytes reproducible across runs.
    target.write_bytes(gzip.compress(("\n".join(out_lines) + "\n").encode("utf-8"), mtime=0))
    return len(out_lines)


def main() -> int:
    """Download, verify and write the bundled data files."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="testroute_fetch_") as tmp:
        folder = Path(tmp)
        try:
            for name, (url, sha) in SOURCES.items():
                print(f"Downloading {name} from {url}")
                download(url, folder / name, sha)
        except (OSError, ValueError) as exc:
            print(f"fetch_datasets: FAILED ({exc})")
            return 1
        shutil.copy2(folder / "HumanEval.jsonl.gz", OUT_DIR / "HumanEval.jsonl.gz")
        count = trim_plus(folder / "HumanEvalPlus.jsonl.gz", OUT_DIR / "humaneval_plus_inputs.jsonl.gz")
    print(f"Wrote {OUT_DIR / 'HumanEval.jsonl.gz'} and {count} input rows to humaneval_plus_inputs.jsonl.gz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
