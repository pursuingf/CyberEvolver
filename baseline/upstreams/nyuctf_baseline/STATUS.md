# NYUCTF Agents Upstream Status

## Cloned Source

- Upstream repo: `https://github.com/NYU-LLM-CTF/nyuctf_agents.git`
- Local path: `baseline/upstreams/nyuctf_baseline/nyuctf_agents`
- Clone mode: shallow clone
- Current HEAD: `6bb4d2b437c09457adb7dba6f15074f57af83a1f`

## Obvious Entrypoints

- `run_baseline.py` for the official NYUCTF baseline agent
- `run_dcipher.py` for the D-CIPHER multi-agent baseline

## Setup Status

- Virtual environment: `baseline/upstreams/nyuctf_baseline/nyuctf_agents/.venv`
- Re-ran the task-card install commands on `2026-04-11` and both completed with exit code `0`:
  - `python -m pip install -r requirements.txt`
  - `python -m pip install -e .`
- Upstream package import smoke check passed in the venv:
  - `python -c "import nyuctf_baseline, nyuctf_multiagent; print(\"upstream_import_ok\")"`

## Architecture Notes

- `run_baseline.py` flows through: argument parsing -> `CTFDataset` -> `CTFChallenge` -> `CTFEnvironment` -> backend construction -> `CTFConversation.run()`.
- `nyuctf_baseline/environment.py` manages the persistent agent tool container and copies challenge files into `~/ctf_files`.
- `nyuctf_baseline/conversation.py` runs the ReAct loop, starts the challenge target, starts the agent tool container, and writes the transcript log.
- `nyuctf_baseline/backends/openai_backend.py` is OpenAI-style, but the upstream implementation does not honor `--api-endpoint` and validates models against a fixed allowlist that does not include `deepseek-v3-ep`.

## Local Runner Integration

- Added runner: `baseline/runners/nyuctf_baseline_runner.py`
- Runner behavior:
  - gets `chal_data` from `CTFManager.get_challenge_data(...)`
  - uses `runtime_args = {"target_scope": "per_challenge"}`
  - reads target host/port from `chal_data["target_info"]`
  - adapts NYUCTF's `CTFChallenge` object without modifying upstream core files
  - passes `chal_data` alongside the upstream prompt manager render path
  - uses an OpenAI-compatible backend shim so `deepseek-v3-ep` can be called against `http://dsv3.sii.edu.cn/v1`
  - preserves overrideable CLI/env entry points for model, auth, endpoint, prompt set, budget, and run naming parameters
  - records an `effective_config` payload in the conversation log for reproducibility and multi-model comparisons
  - isolates the agent tool container with per-run Docker names

## Concurrent Execution Parameters

- Recommended template:
  - `TASK_ID=<task_id>`
  - `RUN_ID=${TASK_ID}-$(date +%s)`
  - `LOG_DIR=baseline/logs/${TASK_ID}/${RUN_ID}`
  - `CONTAINER_NAME=ctfenv-${RUN_ID}`
  - `NETWORK_NAME=ctfnet-${RUN_ID}`
- The runner consumes `TASK_ID`, `RUN_ID`, `LOG_DIR`, `CONTAINER_NAME`, and `NETWORK_NAME` from the environment when provided.

## Verification Notes

- Runner import check passed:
  - `python -c "import baseline.runners.nyuctf_baseline_runner as runner; print(runner.DEFAULT_MODEL, runner.DEFAULT_API_ENDPOINT)"`
- Docker checks passed:
  - `docker image inspect ctfenv`
  - `docker network inspect ctfnet`
- Model endpoint probe passed on `2026-04-11`:
  - `curl http://dsv3.sii.edu.cn/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer sk-test" -d '{"model":"deepseek-v3-ep","messages":[{"role":"user","content":"hello"}],"max_tokens":10}'`
  - observed result: `HTTP/1.1 200 OK` with JSON body containing a `chat.completion` response for model `deepseek-v3-ep`
- Runner still imports after the logging/override updates.

## Notes

- No upstream source files under `nyuctf_baseline/` were modified.
- The upstream repo can now be used for NYUCTF baseline setup work in this workspace.
- Remaining verify-stage work is separate from this setup task and belongs in `t02`.
