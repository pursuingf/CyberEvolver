# NYUCTF D-CIPHER Source Status

- Upstream repo: `https://github.com/NYU-LLM-CTF/nyuctf_agents.git`
- Local clone path: `baseline/upstreams/nyuctf_dcipher/nyuctf_agents`
- Clone mode: shallow clone of `main`
- Current HEAD: `6bb4d2b437c09457adb7dba6f15074f57af83a1f`
- Obvious entrypoint: `python run_dcipher.py`
- Trust status: `verified`

This directory is the upstream source mirror for the NYUCTF D-CIPHER baseline. The repo is kept separate from the local benchmark/runtime integration so the source package can be inspected and imported without overwriting workspace changes.

## Local setup status

- Python environment: `baseline/upstreams/nyuctf_dcipher/nyuctf_agents/.venv`
- Dependency install: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && pip install -e .` completed successfully on 2026-04-11
- Runner wrapper: `baseline/runners/nyuctf_dcipher_runner.py`
- Key file: `baseline/upstreams/nyuctf_dcipher/deepseek_keys.json`

## Architecture notes

- Upstream flow: `run_dcipher.py` loads a per-category YAML config, constructs `AutoPromptAgent`, `PlannerAgent`, and `ExecutorAgent`, then runs `PlannerExecutorSystem.run()`.
- Backend routing: `nyuctf_multiagent.backends.MODELS` maps model names to backend classes. The local runner registers `deepseek-v3-ep` as an OpenAI-compatible model at runtime without modifying `nyuctf_multiagent/`.
- Config loading: `nyuctf_multiagent.utils.load_config()` validates planner, executor, and autoprompter model names against `MODELS`, so the DeepSeek model registration must happen before config instantiation.

## Local integration notes

- `CTFManager` is the source of truth for challenge runtime state.
- The runner reads target connectivity from `chal_data["target_info"]` and builds a thin challenge adapter instead of using the upstream dataset loader.
- The runner copies `chal_data` into the worker container as `ctf_files/chal_data.json` and formats prompt-visible server information from the `target_info` target host and port.
- Upstream `nyuctf_multiagent/` core code was left unchanged. The only upstream-tree edits are task-required YAML config changes so all three agents declare `deepseek-v3-ep` and `api_endpoint: http://dsv3.sii.edu.cn/v1`.
- The runner keeps shared defaults overridable and exposes role-specific override hooks through `--planner-model`, `--executor-model`, `--autoprompter-model`, `--role-overrides`, and repeatable `--set section.field=value` flags.
- Each run records the final effective configuration in a sidecar JSON file next to the main log: `<challenge>.effective-config.json`.
- Model endpoint validation succeeded on 2026-04-11 via `POST http://dsv3.sii.edu.cn/v1/chat/completions` with `Authorization: Bearer sk-test`, returning a successful JSON response for `model=deepseek-v3-ep`.

## Concurrent execution parameters

- `TASK_ID=<task_id>`
- `RUN_ID=${TASK_ID}-$(date +%s)`
- `LOG_DIR=baseline/logs/${TASK_ID}/${RUN_ID}`
- `CONTAINER_NAME=ctfenv-${RUN_ID}`
- `NETWORK_NAME=ctfnet-${RUN_ID}`

The runner defaults to those naming templates through environment variables and creates the worker Docker network on demand.
