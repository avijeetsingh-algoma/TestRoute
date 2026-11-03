# config/

Plain-text YAML configuration for TestRoute (spec Section 9.6). Every setting
is explained by a comment next to it inside each file; this page gives the
overview.

| File | Read by | Purpose |
|---|---|---|
| `models.yaml` | C4 router, C5 providers | Local and cloud model ids, sampling, request timeouts, retry policy, call budgets. Locked by spec Section 2. |
| `pricing.yaml` | `cost.py` | Rough DeepSeek list and off-peak prices, peak window, assumed laptop power and electricity price (FB7, FB10). |
| `experiment_main.yaml` | C1, C8, C9, C10 | Main experiment: results folder, subject group, trials, seeds, condition tiers, quality target, mutation and test runner limits. |
| `experiment_pilot.yaml` | C1 | Pilot overrides (5 pilot functions, 1 trial, Tier 1 and Tier 2). |
| `experiment_demo.yaml` | C1 | Demo overrides used by `run_experiment_demo.bat` (2 subjects, live and replay conditions). |
| `sample_run.yaml` | `cli.py run` | Defaults for a single `run.bat` invocation. |

## The `extends` key

A file with `extends: experiment_main.yaml` is loaded on top of that file.
Top-level keys it lists replace the base values; nested blocks (mappings) are
merged key by key, so an override of one threshold keeps the others. Lists are
replaced whole.

## The `.env` file (project root)

`.env` is created by `setup.bat` from `.env.example` and is never committed.

| Variable | Meaning |
|---|---|
| `DEEPSEEK_API_KEY` | The only secret. Paste your DeepSeek key after the `=`. It is never printed, logged or passed to subprocesses that run generated code. |
| `DEEPSEEK_BASE_URL` | OpenAI-compatible DeepSeek endpoint. Default `https://api.deepseek.com`. |
| `OLLAMA_HOST` | Address of the local Ollama server. Default `http://localhost:11434`. Only needed for local and ESC conditions. |
