# T-Agent / HPTSA Source Status

## Current Status

- Route: `cloned`
- Local source mirror: `baseline/upstreams/t_agent/HPTSA/`
- Upstream repository: `https://github.com/uiuc-kang-lab/HPTSA`
- Clone mode: shallow clone (`--depth 1`)
- Current HEAD: `ab4c77c`
- Canonical venv: `baseline/upstreams/t_agent/.venv/`
- Venv interpreter: Python 3.10.12
- Install command: `.venv/bin/pip install -e HPTSA`
- Additional runner dependency: `.venv/bin/pip install Jinja2`

## Upstream Structure

- Supervisor entrypoint: `src/tagent/main.py`
- Supervisor constructor: `src/tagent/main.py::_create_supervisor_agent`
- Multi-agent dispatch tools: `src/tagent/tools/agent_tools.py`
- Shared agent dependency object: `src/tagent/agent_dependencies.py::AgentDeps`
- Specialized hacker agents:
  - `src/tagent/agents/general_agent.py`
  - `src/tagent/agents/sql_agent.py`
  - `src/tagent/agents/csrf_agent.py`
  - `src/tagent/agents/ssti_agent.py`
  - `src/tagent/agents/xss_agent.py`
  - `src/tagent/agents/zap_agent.py`
- Summarizer agent: `src/tagent/agents/summarizer_agent.py`
- Prompt files: `src/tagent/prompts/*.md`
- Dependency declaration: `pyproject.toml`

## Architecture Notes

HPTSA implements T-Agent as a supervisor-led OpenAI Agents SDK workflow. The supervisor receives the full task prompt and can call tool wrappers such as `call_general_agent`, `call_sql_agent`, `call_csrf_agent`, `call_ssti_agent`, `call_xss_agent`, and `call_zap_agent`. Each wrapper runs one specialized hacker agent, passes the original task prompt plus supervisor-provided context, then summarizes the subagent transcript for the supervisor. The wrapper configures the supervisor, subagents, and summarizer with the same model at runtime.

## Tool Notes

Web exploitation tools are defined under `src/tagent/tools/`:

- `browser_tools.py` / `browser_utils.py`: page fetch, text extraction, hyperlink extraction, element extraction
- `general_tools.py`: `pip_install`, `python_script`, `run_bash`
- `sql_tools.py`: sqlmap wrapper
- `zap_tools.py`: OWASP ZAP baseline scan wrapper
- `agent_tools.py`: supervisor-callable subagent tools

## LLM Configuration

The upstream CLI accepts `--model`, but API key and base URL are environment-driven through the OpenAI Agents SDK. `baseline/runners/t_agent_runner.py` sets the OpenAI-compatible client in its bootstrap process:

- model default: `deepseek-v3-ep`
- API base default: `http://dsv3.sii.edu.cn/v1`
- API key sources: `--api-key`, `T_AGENT_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`
- OpenAI Agents SDK API default: `chat_completions`
- tracing default: disabled

No upstream source files were modified.

## CTFManager Integration

Runner: `baseline/runners/t_agent_runner.py`

The runner:

- creates `CTFEnvConfig` with overridable `benchmark_root`, `run_mode`, `server_url`, and `use_external_access`
- calls `CTFManager.get_challenge_data(challenge_id, auto_init=True, runtime_args=...)`
- defaults CVE-Bench runtime args to `{"parallel_mode": "network", "target_scope": "per_agent"}`
- reads endpoints from `chal_data["target_info"]`
- renders the benchmark prompt profile with `chal_data`
- injects CTFManager reachable endpoints into the prompt passed to the supervisor and all subagents
- runs HPTSA from a log-local working directory so upstream source is not dirtied by `agent_workspace`
- writes `effective_config.json`, `runtime_summary.json`, `chal_data.json`, `prompt.md`, stdout/stderr logs, and `run_result.json`

## Concurrent Execution Parameters

Use these templates for isolated runs:

```bash
TASK_ID=t11
RUN_ID=${TASK_ID}-$(date +%s)
LOG_DIR=baseline/logs/${RUN_ID}
CONTAINER_NAME=ctfenv-${RUN_ID}
NETWORK_NAME=ctfnet-${RUN_ID}
```

For this CVE-Bench baseline, the runner also defaults to CTFManager `target_scope=per_agent`.

## Notes

- A first install attempt with the workspace default Python 3.13 could not use an `lxml==5.2.1` wheel and failed due missing libxml2/libxslt development headers. The canonical `.venv` was recreated with `/usr/bin/python3.10`, which installed successfully from wheels.
- HPTSA does not declare Jinja2 because it does not render this repository's benchmark prompt profiles itself. The wrapper uses Jinja2 to render local CVE-Bench prompt templates, so Jinja2 was installed into the T-Agent venv.
