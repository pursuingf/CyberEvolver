# Cy-Agent Source Status

## Current status

- Route: `cloned`
- Local source mirror: `baseline/upstreams/cy_agent/cybench/`
- Upstream: `https://github.com/andyzorigin/cybench.git`
- Clone mode: shallow clone (`--depth 1`)
- Current upstream HEAD: `d494f24`
- Venv: `baseline/upstreams/cy_agent/.venv/`
- Python: `3.10.12`

## Setup result

`baseline/upstreams/cy_agent/.venv/bin/pip install -r baseline/upstreams/cy_agent/cybench/requirements.txt` completed with exit code 0.

Pip reported resolver warnings from packages that were already present in the venv:

- `pynacl 1.6.2` requires `cffi>=2.0.0`, while CyBench pins `cffi==1.16.0`.
- `typing-inspection 0.4.2` requires `typing-extensions>=4.12.0`, while CyBench pins `typing-extensions==4.7.1`.

These warnings did not block importing CyBench modules or the runner wrapper.

## Upstream structure

- Main single-task runner: `run_task.py`
- Benchmark runner: `run_benchmark.py`
- Agent loop: `agent/agent.py::SimpleAgent`
- Prompt template: `agent/prompt.py`
- Non-HELM model provider entry: `agent/models/non_helm.py`
- Model registry and deployment names: `agent/dataclasses/agent_spec.py`
- Task metadata shape: each task directory has `metadata/metadata.json`, `metadata/requirements.sh`, and `init_script.sh`.

## Agent architecture notes

Cy-Agent is a ReAct-style shell agent:

- `SimpleAgent` keeps a `ChatChain` with system/task messages, model responses, and assistant observations.
- Each iteration sends the truncated chat chain to the model.
- The model response is parsed for either `COMMAND:` or `ANSWER:`.
- `COMMAND:` responses run as shell commands in the agent work directory.
- Command stdout/stderr is appended back as an observation.
- `ANSWER:` responses are compared against the task/subtask answer.
- Memory is controlled by `responses_to_keep`, `observations_to_keep`, and `max_input_tokens`.

## LLM configuration

The upstream non-HELM path expects model names registered in `agent/dataclasses/agent_spec.py`; `deepseek-v3-ep` is not in that registry. To avoid modifying upstream source, `baseline/runners/cy_agent_runner.py` installs runtime hooks during bootstrap:

- patches non-HELM request/tokenization functions to use `openai.OpenAI(api_key=..., base_url=...)`
- defaults to `model=deepseek-v3-ep`
- defaults to `api_base=http://dsv3.sii.edu.cn/v1`
- requires API key from `--api-key`, `CY_AGENT_API_KEY`, `OPENAI_API_KEY`, or `DEEPSEEK_API_KEY`
- records a masked API key and all final effective parameters in the run log directory.

The required model endpoint check returned HTTP 200 JSON:

```bash
curl http://dsv3.sii.edu.cn/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test" \
  -d '{"model":"deepseek-v3-ep","messages":[{"role":"user","content":"hello"}],"max_tokens":10}'
```

## Runner integration

Runner path: `baseline/runners/cy_agent_runner.py`

Default CTFManager config:

- `benchmark_root=./benchmarks`
- `run_mode=remote`
- `server_url=http://10.1.2.146:7900`
- `use_external_access=True`

Default runtime args:

```json
{"parallel_mode": "network", "target_scope": "per_agent"}
```

The runner reads target endpoints from `chal_data["target_info"]`, writes a CyBench-compatible synthetic task scaffold into the log directory, places full `chal_data.json` in the agent workspace, and records:

- `effective_config.json`
- `bootstrap_config.json`
- `runtime_summary.json`
- `run_result.json`
- `events.jsonl`
- bootstrap stdout/stderr logs.

Important override surfaces:

- `--model`
- `--deployment-name`
- `--api-key`
- `--api-base`
- `--runtime-args-json`
- `--ctf-config-json`
- `--agent-config-json`
- `--max-iterations`
- `--iterations-until-hint`
- `--max-input-tokens`
- `--max-output-tokens`
- `--responses-to-keep`
- `--observations-to-keep`
- `--temperature`
- `--command-timeout-seconds`
- `--task-objective`

## Concurrent execution templates

Use these per run/session:

```bash
TASK_ID=t09
RUN_ID=${TASK_ID}-$(date +%s)
LOG_DIR=baseline/logs/${TASK_ID}/${RUN_ID}
CONTAINER_NAME=ctfenv-${RUN_ID}
NETWORK_NAME=ctfnet-${RUN_ID}
```

For CVE-Bench isolation, keep `target_scope=per_agent`.
