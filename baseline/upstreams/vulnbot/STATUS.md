# VulnBot Upstream Status

## Clone result

- Repository: `https://github.com/KHenryAegis/VulnBot.git`
- Clone path: `/data/pxd-team/workspace/fyh/evolve_ctf_agent/baseline/upstreams/vulnbot/VulnBot`
- Clone mode: shallow clone (`--depth 1`)

## Current HEAD

- Commit: `951cbcc456e6ab972fe5015230e8ebf1bd9e32af`
- Branch: `master`

## Obvious entrypoints

- `python cli.py init`
- `python cli.py start -a`
- `python cli.py vulnbot -m {max_interactions}`

## Important docs

- `README.md`
- `Configuration Guide.md`
- `requirements.txt`

## Notes

- The upstream repo is present and reachable locally.
- Virtualenv path: `baseline/upstreams/vulnbot/VulnBot/.venv/`
- `pip install -r requirements.txt` completed with exit code 0 in the VulnBot virtualenv.
- Upstream import-time runtime gaps found during `python cli.py init` and installed into the virtualenv without modifying upstream source:
  - `python-multipart==0.0.26` is required by FastAPI form/file routes imported from `startup.py`.
  - `socksio==1.0.0` is required in this environment because ambient `ALL_PROXY=socks5://...` is visible to `httpx` during `ollama` import.
  - `paramiko==3.5.1` and `PyNaCl==1.5.0` are required by `actions/shell_manager.py`; this keeps upstream-pinned `cffi==1.16.0` intact and `pip check` clean.
- Docker is available. Cached images include `kalilinux/kali-last-release:latest` and `mysql:8.0`.
- MySQL init smoke used existing task-scoped container `vulnbot-mysql-t07-1775886884` on `127.0.0.1:32768` with database/user `vulnbot`.
- RAG/Milvus was intentionally left disabled for setup (`enable_rag: false`) per task scope.
- `python cli.py init` was verified through `baseline/runners/vulnbot_runner.py --phase init`; successful evidence is under `baseline/logs/t07/t07-setup-smoke6/`.
- The generated setup config in `baseline/logs/t07/t07-setup-smoke6/pentest_root/model_config.yaml` uses:
  - `base_url: http://dsv3.sii.edu.cn/v1`
  - `llm_model: openai`
  - `llm_model_name: deepseek-v3-ep`
  - `api_key: sk-test`
- Model endpoint smoke succeeded with HTTP 200 for `POST http://dsv3.sii.edu.cn/v1/chat/completions`.
- Runner import/compile checks passed:
  - `python -c "import baseline.runners.vulnbot_runner as r; ..."`
  - `python -m py_compile baseline/runners/vulnbot_runner.py`
- Runner records effective configuration to each run log directory:
  - `effective_config.json`
  - `runtime_config_summary.json`
  - aggregate JSONL: `baseline/logs/<TASK_ID>/runs.jsonl`
- Concurrency templates:
  - `TASK_ID=<task_id>`
  - `RUN_ID=${TASK_ID}-$(date +%s)`
  - `LOG_DIR=baseline/logs/${TASK_ID}/${RUN_ID}`
  - `CONTAINER_NAME=ctfenv-${RUN_ID}`
  - `NETWORK_NAME=ctfnet-${RUN_ID}`

## Architecture notes

- `cli.py init` disables config auto-reload, creates `PENTEST_ROOT` data/log directories, initializes MySQL tables via `utils.session.create_tables()`, then writes YAML config templates.
- `cli.py start` delegates to `startup.py`, which can launch API and WebUI processes with `-a`, `--api`, or `--webui`.
- `cli.py vulnbot -m <max_interactions>` enters an interactive pentest flow in `pentest.py`.
- `pentest.py` initializes or resumes a session, then dispatches role classes by `current_role_name`.
- Role chain is `Collector -> Scanner -> Exploiter`; each role plans through `_chat`, executes tasks via `WriteCode` / shell tooling, stores plans/tasks/messages in MySQL, and advances by mutating the session role.
- LLM configuration is loaded from `PENTEST_ROOT/model_config.yaml` through `Configs.llm_config`; OpenAI-compatible calls use `api_key`, `base_url`, `llm_model_name`, `temperature`, `history_len`, `context_length`, and `timeout`.
- The runner avoids upstream source edits by generating per-run `PENTEST_ROOT` configs, adding a Python 3.10 `StrEnum` compatibility shim under `_runner_bootstrap/sitecustomize.py`, and calling VulnBot either via CLI phases or a non-interactive programmatic Collector entrypoint.
