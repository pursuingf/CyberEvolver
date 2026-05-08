# AutoPenBench Upstream Status

## Clone Result

- Upstream repo: `https://github.com/lucagioacchini/auto-pen-bench.git`
- Local clone path: `baseline/upstreams/autopenbench_autonomous/auto-pen-bench`
- Clone mode: shallow clone (`--depth 1`)
- Current HEAD: `9c4890a7195896a399339f4b1b0e0c498fdd27cb`

## Expected Entry Points

- Install/setup: `make install`
- Benchmark build: `make build`
- Benchmark test: `make test ctf <category> <task_type> <vm>`
- Paper-style autonomous run mentioned in README:
  - `cd experiments`
  - `bash run_autonomous.sh`

## Notes

- The upstream README points to `setup/setup.sh` for environment setup during `make install`.
- The repository also includes machine creation and validation helpers under `benchmark/`, `setup/`, and `experiments/`.
- Setup completed for task `t05` on 2026-04-11T13:25:28+08:00.
- Virtualenv path: `baseline/upstreams/autopenbench_autonomous/auto-pen-bench/.venv`.
- Install command that passed: `PIP_CACHE_DIR=/tmp/pip-cache-autopenbench .venv/bin/python -m pip install -e .`.
- The default workspace `python` is Python 3.13.9; the first install attempt failed because `instructor==1.5.0` pulled `jiter==0.5.0`, which tried to bootstrap Rust under read-only `/home/pgroup/.cache`. The successful venv was recreated with `/usr/bin/python3.10`, matching the task's Python 3.10+ rule and using prebuilt wheels.
- Additional runner dependencies installed in the same venv for CTFManager integration: `docker`, `sshtunnel`, `Jinja2`, and their transitive requirements. The upstream-required pins were restored afterward: `openai==1.51.0` and `jiter==0.5.0`. `python -m pip check` reports no broken requirements.

## Architecture Summary

- `autopenbench/driver/pentest_driver.py` exposes `PentestDriver(task, flag, target)` plus `reset()`, Docker Compose restart helpers, and Kali SSH setup. The cloned upstream driver does not define the notebook's `driver.step()` method, so the wrapper provides the action-dispatch layer without modifying upstream code.
- `autopenbench/tools/` defines Pydantic action schemas: `ExecuteBash`, `SSHConnect`, `WriteFile`, and `FinalAnswer`.
- `examples/instructor_agent.ipynb` implements the autonomous baseline as an Instructor/OpenAI loop with a dynamic Pydantic union response model and `agent.chat.completions.create(...)`.
- `Makefile` entrypoints are `make install`, `make build`, and `make test <level> <category> <vm>`. `make install` depends on Docker Compose build and `setup/setup.sh`.
- `setup/setup.sh` writes `.env` entries for `AUTOPENBENCH` and `KALISCRIPTS`, installs Docker/Docker Compose if missing, and runs `pip3 install -e .`.

## Runner Integration

- Runner path: `baseline/runners/autopenbench_runner.py`.
- CTFManager runtime args default to `{"target_scope": "per_challenge"}` and can be overridden with `--runtime-args-json`.
- The runner consumes `chal_data` from `CTFManager`, reads target information from `chal_data["target_info"]`, renders the existing AutoPenBench prompt profile with the full `chal_data`, and logs `challenge_data.json`, prompt files, `steps.jsonl`, `result.json`, and `effective_config.json`.
- Public model parameters are overrideable through CLI/env: `--model`, `--api-base`, `--api-key`, `--max-steps`, `--max-tokens`, `--temperature`, `--top-p`, and `--request-overrides-json`.
- AutoPenBench-private parameters are overrideable through `--autopenbench-args-json`, including tool list, command docs, skill descriptions, and command timeout.
- Default model endpoint is DeepSeek-V3.1-s compatible: model `deepseek-v3-ep`, base URL `http://dsv3.sii.edu.cn/v1`. API keys are read from `AUTOPENBENCH_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, or `--api-key`; the runner does not hardcode the key.
- `configs/autopenbench.yaml` currently lists both repo-local `challenge_json` and live `autopenbench` sources, which duplicate AutoPenBench IDs. This runner filters to the live `autopenbench` source by default; use `--benchmark-sources-json` for explicit alternate source selection.

## Environment Requirements

- Docker available: `Docker version 29.1.5`.
- Docker Compose v2 available: `Docker Compose version v2.40.3`.
- Legacy `docker-compose` available for upstream scripts: `docker-compose version 1.29.2`.
- Upstream native AutoPenBench uses a Kali workstation service at `192.168.0.5`, Docker Compose networks under `192.168.0.0/16`, and the `/root/scripts` volume. The CTFManager runner instead treats the CTFManager-provided reachable endpoint as authoritative and does not restart upstream Compose services unless the user explicitly chooses a native path later.

## Concurrent Execution Templates

- `TASK_ID=<task_id>`; for this task, default `TASK_ID=t05`.
- `RUN_ID=${TASK_ID}-$(date +%s)`.
- `LOG_DIR=baseline/logs/${TASK_ID}/${RUN_ID}` by runner default, or caller-provided `LOG_DIR`.
- `CONTAINER_NAME=ctfenv-${RUN_ID}`.
- `NETWORK_NAME=ctfnet-${RUN_ID}`.

## Verification

- Editable install passed in `.venv` with exit code 0.
- Upstream imports passed: `autopenbench`, `PentestDriver`, `ExecuteBash`, `SSHConnect`, `WriteFile`, `FinalAnswer`.
- Runner import passed: `python -c "import baseline.runners.autopenbench_runner"`.
- Runner dry-run passed with `.venv`: `baseline/upstreams/autopenbench_autonomous/auto-pen-bench/.venv/bin/python -m baseline.runners.autopenbench_runner apb-in-vitro-access_control-vm0 --no-auto-init --dry-run --log-dir /tmp/autopenbench-runner-dryrun`.
- Prompt rendering smoke test passed and includes `chal_data` target endpoint fields.
- DeepSeek model endpoint check passed with non-4xx/5xx JSON from `http://dsv3.sii.edu.cn/v1/chat/completions`.
- `python -m pip check` passed inside the venv.
