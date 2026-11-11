# Prompt templates

These are the Deliverable 2 templates (D2 Section 7 and Appendix B), with the text unchanged apart from line wrapping.

| File | Used for | Placeholders |
|---|---|---|
| `T1_system.txt` | System message of every call, including feedback and escalated calls | none |
| `T1_generation.txt` | User message of call 1 | `{MODULE_NAME}`, `{SOURCE_CODE}`, `{ENTRY_POINT}` |
| `T2_feedback.txt` | User message of calls 2 to 4 | `{MODULE_NAME}`, `{SOURCE_CODE}`, `{CURRENT_TESTS}`, `{NOCODE_BLOCK}`, `{EXEC_BLOCK}`, `{COV_BLOCK}`, `{MUT_BLOCK}` |
| `T2_block_exec.txt` | EXEC block (F1 to F4) | `{FAILURE_LINES}` |
| `T2_block_cov.txt` | COV block (F2, F4) | `{UNCOVERED_LINES}` |
| `T2_block_mut.txt` | MUT block (F3, F4) | `{MUTANT_DIFFS}` |
| `T2_block_nocode.txt` | Used when the previous reply had no usable test file | `{NOCODE_REASON}` |

## Filling rules

- Substitute placeholders by literal string replacement, not `str.format`, because source code and tests contain braces.
- A block is inserted only when its feedback type is enabled in the condition and there is something to report. Otherwise its placeholder becomes an empty string.
- `{FAILURE_LINES}`: one line per failing test, `{TEST_NAME}: {ERROR_TYPE}: {ERROR_MESSAGE}`. A whole-file failure (syntax or collection error) uses `(whole file)` as the test name.
- `{UNCOVERED_LINES}`: one line per uncovered line, `line {N}: {SOURCE_LINE}`, then one line per missed branch, `branch from line {N} to line {M} is never taken`.
- `{MUTANT_DIFFS}`: at most 5 surviving feedback-set mutants, each as `Change {k}:` followed by its `-` and `+` lines, as in D2 Appendix B. Never held-out mutants, never TIMEOUT mutants.
- `{CURRENT_TESTS}`: the best test file so far. If no attempt has produced code yet, use `# no test file was returned`.
- `{NOCODE_REASON}`: the recorded error type, for example `NO_CODE` or `TRUNCATED`.
- Calls are stateless: every call sends the system message plus one user message, never chat history.
