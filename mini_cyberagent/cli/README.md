# `mini-cyber` — terminal interface for cybersec_arena

`mini-cyber` mirrors the design of [mini-swe-agent's `mini` CLI](https://github.com/SWE-agent/mini-swe-agent):
a single typer app, rich-styled output, sub-commands for the major workflows.
Once the package is installed (`pip install -e .` from the repo root) the
console script `mini-cyber` becomes available, or you can invoke
`python -m mini_cyberagent.cli ...` directly.

```
mini-cyber <subcommand> [options]
```

## Commands

| Subcommand | Purpose |
|---|---|
| `solve <chal_id>` | Run an agent on a single challenge (interactive debug). Wraps `run_single_debug.py`. |
| `batch --benchmark <bm>` | Multi-challenge non-evolution run. Wraps `run_batch.py`. |
| `evolve` | Drive the evolution loop. Wraps `run_evolve_batch_skill.py`. |
| `inspect [path]` | Render a saved trajectory as colored panels. Without `path`, lists recent trajectory files. |
| `dashboard <run_dir>` | Live TUI dashboard tracking an in-progress evolution run (refreshes every few seconds). |
| `serve` | Start the bench_hub `challenge_server` (or `target_runtime_server` with `--target-runtime`). |
| `models` | Print the table of LLM models declared in `common/configs/model.yml`. |
| `benchmarks` | Print the table of benchmarks visible under `bench_hub/benchmarks/`. |
| `version` | Print Python version, repo root, and key dependency versions. |

Every subcommand starts with the project banner so screenshots and demos look
consistent. Long output uses bordered panels and color-coded sections (cyan
for thoughts, yellow for actions, green for observations, red for failures).

## Examples

```bash
# Single challenge debug
mini-cyber solve ic-crypto-12 -m DeepSeek-V3.1 --step-limit 30

# Batch run
mini-cyber batch --benchmark cybench -m DeepSeek-V3.1 --max-workers 16

# Evolution
mini-cyber evolve --benchmark cybench --challenge-id ic-crypto-12 \
    -m DeepSeek-V3.1 --config-mode evo

# Watch evolution live (in a second terminal while evolve is running)
mini-cyber dashboard logs/evolution_data/ic-crypto-12/<your_run_id>

# Browse trajectories
mini-cyber inspect                           # list recent
mini-cyber inspect logs/.../<chal>_run0.log  # render one

# List model registry
mini-cyber models

# Start the benchmark server
mini-cyber serve --port 8000
```

## Design notes

- **Thin wrapper.** Every subcommand assembles `argv` and shells out to the
  matching `run_*.py`. We never re-implement the underlying logic; this keeps
  the CLI honest and makes it easy to keep in sync as the runners evolve.
- **`--extra` escape hatch.** `solve`, `batch`, and `evolve` each accept
  `--extra` repeated, which forwards extra raw flags to the underlying
  script. Useful for one-off experiments without growing the typer schema.
- **No global state.** The CLI doesn't maintain its own session or cache.
  Trajectories are read directly from `logs/`; model definitions from
  `common/configs/model.yml`; benchmarks from `bench_hub/benchmarks/`.
- **Rich theme.** All visual constants live in `mini_cyberagent/cli/_theme.py`.
  Override the theme by editing that file once; every subcommand picks up the
  change.
