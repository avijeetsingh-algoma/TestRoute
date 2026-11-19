# experiments/subjects/

Benchmark data and the selected functions under test (spec Section 9.1).

| File | What it is |
|---|---|
| `HumanEval.jsonl.gz` | The 164 HumanEval problems, unchanged from github.com/openai/human-eval (MIT licence). SHA-256 b796127e...79ef. |
| `humaneval_plus_inputs.jsonl.gz` | Per problem: the inputs of HumanEval's own tests (`base_input`) and a seeded sample (seed 20261005) of at most 200 HumanEval+ inputs (`plus_input`), from the EvalPlus v0.1.10 release (Apache-2.0 licence). Only inputs are kept. |
| `selection.csv` | Every problem with the result of each screening criterion (`pass`, `fail` or `not_checked` after an earlier failure), its first failed criterion, its band and its group (`main`, `pilot` or blank). |
| `functions/<HE_id>.py` | The 35 selected subjects: HumanEval's prompt (signature, docstring, any helpers) followed by the canonical solution, exactly as the models see it. |

Both data files were produced once by `tools/fetch_datasets.py`, which checks
the SHA-256 of each download; the grader needs no network for data.

The benchmark inputs are used only for screening and for differential
execution in the equivalence pipeline (spec Section 6.4). They are never shown
to a model and never used to score generated tests.

## Screening criteria (in order; later checks are skipped after a failure)

1. `parses`: the module parses and defines the entry point.
2. `no_io_clock_random`: no file, network, clock or random use (static scan).
3. `max_80_lines`: at most 80 lines.
4. `min_2_decisions`: at least 2 decision points in the entry point and its helpers.
5. `import_under_1s`: imports in under 1 s in a sandboxed subprocess.
6. `passes_humaneval_tests`: the reference passes HumanEval's own `check`.
7. `deterministic`: identical outputs on all bundled inputs under two hash seeds.
8. `min_10_mutants`: at least 10 mutants after TCE and duplicate removal.

Eligible problems are ranked by mutant count into three equal bands
(easy, medium, hard). 35 are drawn with seed 20261005 in proportion to band
size, and 5 of them go to the pilot, again in proportion to band size.

Reproduce with `python -m testroute prepare-subjects`.
