# experiments/equivalence/

Equivalent-mutant pipeline (spec Section 6.4, FB2). This is the only manual
step of the project, and it happens before the pilot, independent of any LLM
output.

| File | Written by | What it is |
|---|---|---|
| `differential/<function_id>.json` | `prepare-mutants` | Differential execution verdict per mutant: label (DIFF_OUTPUT, DIFF_TIMEOUT_ONLY, NO_DIFF), inputs compared, first differing input, timeouts. |
| `differential_summary.csv` | `prepare-mutants` | Counts per function: generated, TCE_EQUIVALENT, DUPLICATE, DIFF_OUTPUT, DIFF_TIMEOUT_ONLY, NO_DIFF. |
| `review_<function_id>.md` | `review-equivalence` | Review sheet: the original function, the diff of every NO_DIFF mutant and the differential evidence. |
| `equivalence_review.csv` | `review-equivalence`, then the two reviewers | One row per NO_DIFF mutant. |

## How to review (both students)

1. Open `review_<function_id>.md` next to `equivalence_review.csv`.
2. Each reviewer, independently, writes `EQUIVALENT`, `NOT_EQUIVALENT` or
   `UNSURE` in their column (`reviewer_1`, `reviewer_2`).
3. Compare; settle disagreements together and write the agreed label in
   `final_label`. For `NOT_EQUIVALENT`, `justification` must give a
   distinguishing input (for example `f([1, 1]) returns 2, mutant returns 1`).
   For the other labels, a short reason.
4. Run `python -m testroute prepare-mutants --finalize`. It refuses to run
   while any row lacks a valid `final_label`, then writes the final
   `experiments/mutants/<function_id>.json` files (labels applied, cap and
   split done) and `results/main/analysis/equivalence_summary.csv`.

The code never fills the label columns, and rerunning `review-equivalence`
keeps every label already in the CSV.

EQUIVALENT mutants are removed from every score denominator. UNSURE mutants
stay in the denominator of the primary score; the analysis also reports a
sensitivity score with UNSURE removed (spec Section 6.7).
