"""Category B agent (Mode A): nyuctf_baseline via subprocess.

V4 approach: minimal intervention. We generate a temp dataset JSON and
a small Python launch script, then run the upstream agent as a subprocess.

What we control:
  - Target info: injected via temp dataset JSON (box=inner_host, internal_port=inner_port)
  - LLM routing: OPENAI_API_BASE + OPENAI_API_KEY env vars
  - Container network: --network ctfnet
  - Max rounds: --max-rounds

What upstream controls natively:
  - Agent container creation (CTFEnvironment.start_docker)
  - Tool execution (CommandExec via docker exec)
  - LLM client (OpenAI SDK reads OPENAI_API_BASE from env)
  - Conversation loop (CTFConversation.run)
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

_UPSTREAM_ROOT = Path(__file__).resolve().parent.parent / "upstreams" / "nyuctf_baseline" / "nyuctf_agents"

_UPSTREAM_VENV_PYTHON = _UPSTREAM_ROOT / ".venv" / "bin" / "python"

_CHECK_FLAG_SUCCESS_PATTERNS = (
    re.compile(r"check_flag:\s*\{'correct':\s*True\}", re.IGNORECASE),
    re.compile(r'check_flag:\s*\{"correct":\s*true\}', re.IGNORECASE),
    re.compile(r"Correct flag!", re.IGNORECASE),
)


def _resolve_container_home(container_image: str) -> str:
    """Return the effective home directory inside the agent container."""
    if container_image == "ctfenv":
        return "/root"
    return "/home/ctfplayer"


def _has_successful_check_flag(stdout: str) -> bool:
    """Return True only when upstream logs show a successful flag check."""
    return any(pattern.search(stdout) for pattern in _CHECK_FLAG_SUCCESS_PATTERNS)


def _determine_solved_status(*, stdout: str, parsed_solved: bool, steps: int) -> bool:
    """Mark solved only after a real post-prompt success signal."""
    if parsed_solved and steps > 0:
        return True
    if steps > 0 and _has_successful_check_flag(stdout):
        return True
    return False

_LAUNCH_SCRIPT = """\
import sys
import os
import json
import subprocess
from pathlib import Path

# Add upstream to path
sys.path.insert(0, "{upstream_root}")

from nyuctf.dataset import CTFDataset
from nyuctf.challenge import CTFChallenge
from nyuctf_baseline.environment import CTFEnvironment
from nyuctf_baseline.conversation import CTFConversation
from nyuctf_baseline.prompts.prompts import PromptManager
from nyuctf_baseline.backends import OpenAIBackend
from nyuctf_baseline.backends.utils import MODEL_INFO

# Register the model so OpenAIBackend validation passes and pricing info exists
_model = "{model}"
if _model not in OpenAIBackend.MODELS:
    OpenAIBackend.MODELS.append(_model)
if _model not in MODEL_INFO.get("openai", {{}}):
    MODEL_INFO["openai"][_model] = {{"cost_per_input_token": 0, "cost_per_output_token": 0}}

# Monkey-patch tiktoken to fall back to cl100k_base for unknown models
import tiktoken as _tiktoken
_orig_encoding_for_model = _tiktoken.encoding_for_model
def _encoding_for_model_fallback(model_name, **kw):
    try:
        return _orig_encoding_for_model(model_name, **kw)
    except (KeyError, ValueError):
        return _tiktoken.get_encoding("cl100k_base")
_tiktoken.encoding_for_model = _encoding_for_model_fallback

dataset = CTFDataset(dataset_json="{dataset_json}")
challenge = CTFChallenge(dataset.get("{chal_name}"), dataset.basedir)

environment = CTFEnvironment(challenge, "{container_image}", "{network}")
_container_home = Path("{container_home}")
# Patch the property only — do NOT mutate os.environ["HOME"].
# Upstream's path expansion uses `self.environment.container_home` directly
# (see tools/tools.py `_expanduser` and environment.py `copy_into_container`),
# so the property is sufficient. Setting HOME=/root on the host-side subprocess
# makes the docker CLI look for /root/.docker/config.json and emit a
# "permission denied" warning on every `docker exec`, which then leaks into
# every tool result's stderr and confuses the mini_cyberagent.
CTFEnvironment.container_home = property(lambda self: _container_home)

# --- Monkey-patch CTFEnvironment to use our pre-created container ---
# Upstream's start_docker() runs "docker run -d --rm ctfenv" without a
# keep-alive command, so the container exits immediately.  Instead, we
# inject the container ID that our DockerEnvironment already started
# (which sleeps for 2h) and skip start/stop entirely.
_our_container = "{agent_container_id}"

environment.container = _our_container

_orig_setup = environment.setup
def _patched_setup():
    # Skip start_docker — container is already running.
    # Just run tool setup and file copy.
    for tool in environment.available_tools.values():
        tool.setup()
    for file in environment.challenge.files:
        hostpath = environment.challenge.challenge_dir / file
        environment.copy_into_container(hostpath, f"ctf_files/{{file}}")
environment.setup = _patched_setup

_orig_teardown = environment.teardown
def _patched_teardown(exc_type, exc_value, tb):
    # Only tear down tools, do NOT stop the container (we manage it).
    for tool in environment.available_tools.values():
        tool.teardown(exc_type, exc_value, tb)
environment.teardown = _patched_teardown

# Skip target container management — ChallengeClient handles that.
_orig_start = challenge.start_challenge_container
_orig_stop = challenge.stop_challenge_container
challenge.start_challenge_container = lambda: None
challenge.stop_challenge_container = lambda: None

prompt_manager = PromptManager()

# OPENAI_BASE_URL is read by the OpenAI SDK from env automatically.
# But the upstream OpenAIBackend constructs OpenAI(api_key=...) without
# passing base_url, so we must patch the client after construction.
from argparse import Namespace
_args = Namespace(hints=False, box=None, yes=False, no_docker=False)

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

backend = OpenAIBackend(
    prompt_manager.system_message(challenge),
    prompt_manager.hints_message(),
    environment.available_tools,
    model=_model,
    api_key=os.environ.get("OPENAI_API_KEY"),
    args=_args,
)
backend.client = _wrap_chat_create(backend.client)
_base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
if _base_url:
    from openai import OpenAI as _OpenAI
    backend.client = _wrap_chat_create(_OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=_base_url))

logfile = Path("{logfile}")
logfile.parent.mkdir(parents=True, exist_ok=True)

# --- Token accounting ---
# Upstream's backend.send() computes in_token / out_token per call for pricing
# but discards them.  Wrap it so we can sum cumulative totals and emit them in
# the final JSON for worker.py -> result.json.
backend._token_in_total = 0
backend._token_out_total = 0
_orig_send = backend.send
def _send_with_tokens(message=None):
    _in_before = backend.count_tokens(message) if message else 0
    content, tool_calls, cost = _orig_send(message)
    _out_after = backend.count_tokens(content)
    backend._token_in_total += _in_before
    backend._token_out_total += _out_after
    return content, tool_calls, cost
backend.send = _send_with_tokens

with CTFConversation(environment, challenge, prompt_manager, backend, logfile, max_rounds={max_rounds}, max_cost={max_cost}, args=_args) as convo:
    convo.run()

result = {{
    "solved": environment.solved,
    "rounds": convo.rounds,
    "tokens_input": int(getattr(backend, "_token_in_total", 0)),
    "tokens_output": int(getattr(backend, "_token_out_total", 0)),
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
    """Run nyuctf_baseline agent via subprocess.

    Generates a temp dataset JSON with target info (inner_host/inner_port),
    then runs the upstream CTFConversation as a subprocess.
    """
    _log = logger_instance or logger
    start_time = time.time()

    step_limit = kwargs.get("step_limit", 10)
    model_kwargs = kwargs.get("model_kwargs", {})
    model_name = model_kwargs.get("model", "gpt-4o-2024-05-13")

    target = resolve_target_info(chal_data)
    flag = chal_data.get("flag", "")
    chal_id = chal_data.get("id", "")

    llm_env = build_llm_env(model_kwargs)

    with TempConfigDir("nyuctf_single_") as tmp:
        try:
            # Build a minimal dataset JSON that CTFDataset can load.
            # Format: {canonical_name: {year, event, category, path}}
            # The challenge data lives in a subdirectory with challenge.json
            chal_name = chal_id
            category = chal_data.get("category", "misc")

            dataset_data = {
                chal_name: {
                    "year": chal_data.get("year", "2024"),
                    "event": chal_data.get("event", "custom"),
                    "category": category,
                    "path": chal_name,
                }
            }
            dataset_path = tmp.write_json("dataset.json", dataset_data)

            # Create challenge directory with challenge.json
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
                "compose": False,  # Don't let upstream start/stop target containers
            }
            tmp.write_json(f"{chal_name}/challenge.json", challenge_json)

            # Write launch script
            logfile = tmp.path / "conversation.json"
            container_image = docker_env.config.image if hasattr(docker_env, "config") else "ctfenv"
            script = _LAUNCH_SCRIPT.format(
                upstream_root=str(_UPSTREAM_ROOT),
                dataset_json=str(dataset_path),
                chal_name=chal_name,
                container_image=container_image,
                container_home=_resolve_container_home(container_image),
                network="ctfnet",
                model=model_name,
                logfile=str(logfile),
                max_rounds=step_limit,
                max_cost=kwargs.get("max_cost", 10.0),
                agent_container_id=docker_env.container_id or "",
            )
            script_path = tmp.write_text("launch.py", script)

            # Run as subprocess using upstream venv Python (falls back to sys.executable)
            python_bin = str(_UPSTREAM_VENV_PYTHON) if _UPSTREAM_VENV_PYTHON.is_file() else sys.executable
            # Save upstream.log to persistent dir alongside agent.log
            upstream_log = Path(kwargs["log_dir"]) / "upstream.log" if kwargs.get("log_dir") else tmp.path / "upstream.log"
            result = run_upstream_subprocess(
                [python_bin, str(script_path)],
                timeout=kwargs.get("timeout", 900.0),
                env=llm_env,
                log_path=upstream_log,
                logger_instance=_log,
            )

            elapsed = time.time() - start_time

            # Parse output for result
            stdout = result["stdout"]
            parsed_solved = False
            steps = 0
            tokens_input = 0
            tokens_output = 0

            # Try to parse JSON result from stdout (last line)
            for line in reversed(stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        parsed = json.loads(line)
                        parsed_solved = parsed.get("solved", False)
                        steps = parsed.get("rounds", 0)
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
                            steps = conv_data.get("rounds", len(conv_data.get("messages", [])))
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
            _log.error("nyuctf_single error: %s", exc, exc_info=True)
            return make_result(error=str(exc), elapsed_seconds=elapsed)
