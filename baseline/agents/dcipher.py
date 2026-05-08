"""Category B agent (Mode A): dcipher (nyuctf_multiagent) via subprocess.

V4 approach: minimal intervention. We generate a temp dataset JSON,
a keys.cfg, and a config YAML, then run the upstream planner-executor
as a subprocess.

What we control:
  - Target info: injected via temp dataset JSON (box=inner_host, internal_port=inner_port)
  - LLM routing: keys.cfg + OPENAI_API_BASE env var
  - Container network: --container-network ctfnet
  - Model name: via config YAML

What upstream controls natively:
  - Agent container creation (CTFEnvironment.start_docker)
  - Tool execution (CommandExec, CheckFlag, etc.)
  - LLM client (OpenAI SDK reads OPENAI_API_BASE from env)
  - Planner-executor loop
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from baseline.agents.upstream_runner import (
    TempConfigDir,
    build_llm_env,
    make_result,
    resolve_target_info,
    run_upstream_subprocess,
    stage_challenge_files,
)

logger = logging.getLogger(__name__)

_UPSTREAM_ROOT = Path(__file__).resolve().parent.parent / "upstreams" / "nyuctf_dcipher" / "nyuctf_agents"
_VENV_PYTHON = _UPSTREAM_ROOT / ".venv" / "bin" / "python"

_SUBMIT_FLAG_SUCCESS_PATTERNS = (
    re.compile(r"submit_flag:\s*\{'success':\s*True\}", re.IGNORECASE),
    re.compile(r'submit_flag:\s*\{"success":\s*true\}', re.IGNORECASE),
    # Must NOT match "Challenge Not Solved!" — require start-of-line or
    # non-word character before SOLVED, and reject "Not" immediately before.
    re.compile(r"(?<!Not )SOLVED!", re.IGNORECASE),
)


def _has_successful_submit_flag(stdout: str) -> bool:
    """Return True only when upstream logs show a successful flag submission."""
    return any(pattern.search(stdout) for pattern in _SUBMIT_FLAG_SUCCESS_PATTERNS)


def _determine_solved_status(*, stdout: str, parsed_solved: bool, steps: int) -> bool:
    """Trust solved only after a real tool-success signal or nonzero solved run."""
    if parsed_solved and steps > 0:
        return True
    if steps > 0 and _has_successful_submit_flag(stdout):
        return True
    return False

_LAUNCH_SCRIPT = """\
import sys
import os
import json
import subprocess
from pathlib import Path

sys.path.insert(0, "{upstream_root}")

from nyuctf.dataset import CTFDataset
from nyuctf.challenge import CTFChallenge
from nyuctf_multiagent.environment import CTFEnvironment
from nyuctf_multiagent.backends import MODELS, Role
from nyuctf_multiagent.backends.openai_backend import OpenAIBackend
from nyuctf_multiagent.prompting import PromptManager
from nyuctf_multiagent.agent import BaseAgent, PlannerExecutorSystem, PlannerAgent, ExecutorAgent, AutoPromptAgent
from nyuctf_multiagent.utils import APIKeys, get_log_filename, load_config
from nyuctf_multiagent.config import Config

dataset = CTFDataset(dataset_json="{dataset_json}")
challenge = CTFChallenge(dataset.get("{chal_name}"), dataset.basedir)

keys = APIKeys("{keys_cfg}")
environment = CTFEnvironment(challenge, "{container_image}", "{network}")

# --- Monkey-patch CTFEnvironment to use our pre-created container ---
_our_container = "{agent_container_id}"
environment.container = _our_container

_orig_setup = environment.setup
def _patched_setup():
    for tool in environment.tools.values():
        tool.setup()
    for file in environment.challenge.files:
        hostpath = environment.challenge.challenge_dir / file
        environment.copy_into_container(hostpath, f"ctf_files/{{file}}")
environment.setup = _patched_setup

def _patched_teardown(exc_type, exc_value, tb):
    for tool in environment.tools.values():
        tool.teardown(exc_type, exc_value, tb)
environment.teardown = _patched_teardown

# Skip target container management — ChallengeClient handles that
challenge.start_challenge_container = lambda: None
challenge.stop_challenge_container = lambda: None

# Upstream marks solved if the flag substring appears anywhere in prompts,
# assistant messages, or tool results. Disable that shortcut so only actual
# submit_flag success can solve the challenge.
def _patched_check_flag_in_response(self, response):
    return
BaseAgent.check_flag_in_response = _patched_check_flag_in_response

# Register custom model names into OpenAIBackend.MODELS before load_config validates.
# Custom models are routed via OPENAI_API_BASE (OpenAI-compatible endpoint).
_default_model_cfg = {{"max_context": 128000, "cost_per_input_token": 0, "cost_per_output_token": 0}}
for _m in ["{model_name}"]:
    if _m not in OpenAIBackend.MODELS:
        OpenAIBackend.MODELS[_m] = _default_model_cfg
    if _m not in MODELS:
        MODELS[_m] = OpenAIBackend

# Build a minimal args namespace for load_config
from argparse import Namespace
_args = Namespace(planner_model=None, executor_model=None, autoprompter_model=None, max_cost=-1, enable_autoprompt=False)
config = load_config(Path("{config_yaml}"), args=_args)

_openai_key = keys['OPENAI']

def _load_openai_extra_body():
    raw = os.environ.get("OPENAI_EXTRA_BODY", "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None

_OPENAI_EXTRA_BODY = _load_openai_extra_body()

def _wrap_chat_create(client):
    if not _OPENAI_EXTRA_BODY or client is None:
        return client
    _orig_create = client.chat.completions.create
    def _create_with_extra_body(*args, **kwargs):
        extra_body = dict(kwargs.get("extra_body") or {{}})
        extra_body.update(_OPENAI_EXTRA_BODY)
        kwargs["extra_body"] = extra_body
        return _orig_create(*args, **kwargs)
    client.chat.completions.create = _create_with_extra_body
    return client

planner_backend_cls = MODELS.get(config.planner.model, OpenAIBackend)
planner_backend = planner_backend_cls(
    Role.PLANNER, config.planner.model,
    environment.get_toolset(config.planner.toolset),
    _openai_key, config,
)
planner_prompter = PromptManager(Path("{config_yaml}").parent / config.planner.prompt, challenge, environment)
planner = PlannerAgent(environment, challenge, planner_prompter,
                       planner_backend, max_rounds=config.planner.max_rounds)

executor_backend_cls = MODELS.get(config.executor.model, OpenAIBackend)
executor_backend = executor_backend_cls(
    Role.EXECUTOR, config.executor.model,
    environment.get_toolset(config.executor.toolset),
    _openai_key, config,
)
executor_prompter = PromptManager(Path("{config_yaml}").parent / config.executor.prompt, challenge, environment)
executor = ExecutorAgent(environment, challenge, executor_prompter,
                         executor_backend, max_rounds=config.executor.max_rounds)
executor.conversation.len_observations = config.executor.len_observations

autoprompter_backend_cls = MODELS.get(config.autoprompter.model, OpenAIBackend)
autoprompter_backend = autoprompter_backend_cls(
    Role.AUTOPROMPTER, config.autoprompter.model,
    environment.get_toolset(config.autoprompter.toolset),
    _openai_key, config,
)
autoprompter_prompter = PromptManager(Path("{config_yaml}").parent / config.autoprompter.prompt, challenge, environment)
autoprompter = AutoPromptAgent(environment, challenge, autoprompter_prompter,
                               autoprompter_backend, max_rounds=config.autoprompter.max_rounds)

# --- Patch OpenAI clients to use our dispatcher endpoint ---
# The upstream OpenAIBackend constructs OpenAI(api_key=...) without passing
# base_url, so requests go to api.openai.com by default.  We must patch
# all three backends to route through our LLM dispatcher.
_base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
if _base_url:
    from openai import OpenAI as _OpenAI
    for _backend in (planner_backend, executor_backend, autoprompter_backend):
        _backend.client = _wrap_chat_create(_OpenAI(api_key=_openai_key, base_url=_base_url))
else:
    for _backend in (planner_backend, executor_backend, autoprompter_backend):
        _backend.client = _wrap_chat_create(_backend.client)

if config.experiment.enable_autoprompt:
    autoprompter.enable_autoprompt()

# --- Token accounting ---
# Wrap each backend's _call_model so we can read response.usage.prompt_tokens
# and completion_tokens off every API response.  The backend itself only
# surfaces dollar cost (see upstream openai_backend.calculate_cost), so the
# raw completion object is the only place token counts are exposed.
_token_totals = {{"tokens_input": 0, "tokens_output": 0}}
def _wrap_call_model(backend):
    _orig = backend._call_model
    def _wrapped(*args, **kwargs):
        resp = _orig(*args, **kwargs)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _token_totals["tokens_input"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            _token_totals["tokens_output"] += int(getattr(usage, "completion_tokens", 0) or 0)
        return resp
    backend._call_model = _wrapped
for _b in (planner_backend, executor_backend, autoprompter_backend):
    _wrap_call_model(_b)

logfile = Path("{logfile}")
logfile.parent.mkdir(parents=True, exist_ok=True)

with PlannerExecutorSystem(environment, challenge, autoprompter, planner, executor,
                           max_cost=config.experiment.max_cost, logfile=logfile) as system:
    system.run()

_planner_rounds = planner.conversation.round if hasattr(planner, "conversation") else 0
_executor_rounds = sum(e.conversation.round for e in system.all_executors) if hasattr(system, "all_executors") else 0
result = {{
    "solved": environment.solved,
    "planner_rounds": _planner_rounds,
    "executor_rounds": _executor_rounds,
    "total_rounds": _planner_rounds + _executor_rounds,
    "tokens_input": int(_token_totals["tokens_input"]),
    "tokens_output": int(_token_totals["tokens_output"]),
}}
print(json.dumps(result))
"""


def run_challenge(
    chal_data: dict,
    docker_env: Any,
    llm_stub: Any,
    logger_instance: Optional[logging.Logger] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Run dcipher (nyuctf_multiagent) agent via subprocess."""
    _log = logger_instance or logger
    start_time = time.time()

    model_kwargs = kwargs.get("model_kwargs", {})
    model_name = model_kwargs.get("model", "gpt-4o-2024-05-13")

    target = resolve_target_info(chal_data)
    flag = chal_data.get("flag", "")
    chal_id = chal_data.get("id", "")

    llm_env = build_llm_env(model_kwargs)

    with TempConfigDir("dcipher_") as tmp:
        try:
            chal_name = chal_id
            category = chal_data.get("category", "misc")

            # Dataset JSON
            dataset_data = {
                chal_name: {
                    "year": chal_data.get("year", "2024"),
                    "event": chal_data.get("event", "custom"),
                    "category": category,
                    "path": chal_name,
                }
            }
            dataset_path = tmp.write_json("dataset.json", dataset_data)

            # Challenge directory with challenge.json
            chal_dir = tmp.ensure_dir(chal_name)

            stage_challenge_files(chal_data, chal_dir, logger_instance=_log)

            challenge_json = {
                "name": chal_name,
                "category": category,
                "description": chal_data.get("description", "").replace(
                    "{box}", target["container_host"]
                ).replace(
                    "{port}", str(target["inner_port"])
                ).replace(
                    "{{box}}", target["container_host"]
                ).replace(
                    "{{port}}", str(target["inner_port"])
                ),
                "flag": flag,
                "points": chal_data.get("points", 0),
                "box": target["container_host"],
                "internal_port": target["inner_port"],
                "files": chal_data.get("files", []),
                "compose": False,
            }
            tmp.write_json(f"{chal_name}/challenge.json", challenge_json)

            # keys.cfg — OPENAI_API_BASE env var handles the endpoint
            api_key = model_kwargs.get("openai_api_key", "sk-dummy")
            keys_path = tmp.write_text("keys.cfg", f"OPENAI={api_key}\n")

            # Config YAML — override model names, enable autoprompt
            config_yaml_content = f"""\
experiment:
  max_cost: {kwargs.get('max_cost', 1.0)}
  enable_autoprompt: True

planner:
  max_rounds: {kwargs.get('step_limit', 10)}
  model: {model_name}
  temperature: 0.7
  top_p: 1.0
  max_tokens: 4096
  prompt: prompts/base_planner_prompt.yaml
  toolset:
    - run_command
    - submit_flag
    - giveup
    - delegate

executor:
  max_rounds: {kwargs.get('step_limit', 10)}
  model: {model_name}
  temperature: 0.7
  top_p: 1.0
  max_tokens: 4096
  len_observations: 5
  prompt: prompts/base_executor_prompt.yaml
  toolset:
    - run_command
    - finish_task
    - disassemble
    - decompile
    - create_file

autoprompter:
  max_rounds: 5
  model: {model_name}
  temperature: 0.7
  top_p: 1.0
  max_tokens: 4096
  prompt: prompts/autoprompt_prompt.yaml
  toolset:
    - run_command
    - generate_prompt
"""
            # Try to use upstream's existing config YAML as base
            upstream_config = _UPSTREAM_ROOT / "configs" / "dcipher" / f"{category}_planner_executor.yaml"
            if upstream_config.exists():
                config_path = tmp.path / "config.yaml"
                config_path.write_text(upstream_config.read_text())
                # Copy prompts directory so relative prompt paths resolve
                upstream_prompts = upstream_config.parent / "prompts"
                if upstream_prompts.exists():
                    import shutil as _shutil
                    dst_prompts = tmp.path / "prompts"
                    if not dst_prompts.exists():
                        _shutil.copytree(upstream_prompts, dst_prompts)
                # Override model
                _override_model_in_yaml(config_path, model_name)
            else:
                config_path = tmp.write_text("config.yaml", config_yaml_content)

            logfile = tmp.path / "conversation.json"
            script = _LAUNCH_SCRIPT.format(
                upstream_root=str(_UPSTREAM_ROOT),
                dataset_json=str(dataset_path),
                chal_name=chal_name,
                keys_cfg=str(keys_path),
                container_image=docker_env.config.image if hasattr(docker_env, "config") else "ctfenv:multiagent",
                network="ctfnet",
                config_yaml=str(config_path),
                logfile=str(logfile),
                model_name=model_name,
                agent_container_id=docker_env.container_id or "",
            )
            script_path = tmp.write_text("launch.py", script)

            python_bin = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
            upstream_log = Path(kwargs["log_dir"]) / "upstream.log" if kwargs.get("log_dir") else tmp.path / "upstream.log"
            result = run_upstream_subprocess(
                [python_bin, str(script_path)],
                timeout=kwargs.get("timeout", 2400.0),
                env=llm_env,
                log_path=upstream_log,
                logger_instance=_log,
            )

            elapsed = time.time() - start_time

            stdout = result["stdout"]
            parsed_solved = False
            steps = 0
            tokens_input = 0
            tokens_output = 0

            for line in reversed(stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        parsed = json.loads(line)
                        parsed_solved = parsed.get("solved", False)
                        steps = parsed.get("total_rounds", parsed.get("planner_rounds", 0))
                        tokens_input = int(parsed.get("tokens_input", 0) or 0)
                        tokens_output = int(parsed.get("tokens_output", 0) or 0)
                        break
                    except json.JSONDecodeError:
                        continue

            # Fallback: try reading conversation.json for step count if process
            # timed out or crashed without printing a JSON result.
            if steps == 0 and (result.get("timed_out") or result.get("returncode", -1) != 0):
                conv_log = tmp.path / "conversation.json"
                if conv_log.is_file():
                    try:
                        conv_data = json.loads(conv_log.read_text())
                        if isinstance(conv_data, list):
                            steps = len(conv_data)
                        elif isinstance(conv_data, dict):
                            steps = len(conv_data.get("rounds", conv_data.get("messages", [])))
                    except (json.JSONDecodeError, OSError):
                        pass

            solved = _determine_solved_status(
                stdout=stdout,
                parsed_solved=parsed_solved,
                steps=steps,
            )

            return make_result(
                solved=solved,
                steps_completed=steps,
                elapsed_seconds=elapsed,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                error=result.get("stderr", "")[:2000] if result.get("returncode", -1) != 0 else None,
            )

        except Exception as exc:
            elapsed = time.time() - start_time
            _log.error("dcipher error: %s", exc, exc_info=True)
            return make_result(error=str(exc), elapsed_seconds=elapsed)


def _override_model_in_yaml(yaml_path: Path, model_name: str) -> None:
    """Override model name and enable autoprompt in a dcipher config YAML."""
    try:
        import yaml
        with yaml_path.open() as f:
            data = yaml.safe_load(f)
        for section in ("planner", "executor", "autoprompter"):
            if section in data and isinstance(data[section], dict):
                data[section]["model"] = model_name
        # Enable autoprompt
        if "experiment" not in data:
            data["experiment"] = {}
        data["experiment"]["enable_autoprompt"] = True
        with yaml_path.open("w") as f:
            yaml.dump(data, f, default_flow_style=False)
    except Exception:
        pass
