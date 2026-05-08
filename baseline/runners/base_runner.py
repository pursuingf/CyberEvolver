"""Shared setup/teardown/logging logic for running a single baseline agent challenge.

This module provides the common lifecycle that every baseline agent runner
follows:

1.  Parse config and resolve the agent module via ``AGENT_REGISTRY``.
2.  Create a ``ChallengeClient`` with per-agent ``runtime_args`` so that each
    agent gets its own isolated target container.
3.  Retrieve challenge data with ``target_scope="per_agent"``.
4.  Spin up a ``DockerEnvironment`` connected to the target network.
5.  Build an ``LLMClientStub`` through ``LLMDispatcherRuntime``.
6.  Copy challenge files into the agent container.
7.  Call the agent's ``run_challenge()`` entry-point.
8.  Save ``result.json`` and clean everything up in ``finally`` blocks.
"""

from __future__ import annotations

import importlib
import json
import logging
import multiprocessing
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from baseline.runtime_policy import docker_config_for_challenge, runtime_args_for_agent
from common.agent_runtime.challenge_client import ChallengeClientConfig, ChallengeClient
from common.agent_runtime.docker_env import DockerEnvironment, DockerEnvironmentConfig
from common.llm_dispatch.dispatcher import LLMDispatcherRuntime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent registry – maps short agent names to importable module paths
# ---------------------------------------------------------------------------

AGENT_REGISTRY: Dict[str, str] = {
    "nyuctf_single": "baseline.agents.nyuctf_single",
    "autopenbench": "baseline.agents.autopenbench",
    "cy_agent": "baseline.agents.cy_agent",
    "dcipher": "baseline.agents.dcipher",
    "t_agent": "baseline.agents.t_agent",
    "vulnbot": "baseline.agents.vulnbot",
}


def get_agent_module(agent_name: str):
    """Import and return the agent module identified by *agent_name*.

    Raises:
        KeyError: If *agent_name* is not in :data:`AGENT_REGISTRY`.
        ImportError: If the module cannot be imported.
    """
    if agent_name not in AGENT_REGISTRY:
        raise KeyError(
            f"Unknown agent '{agent_name}'. "
            f"Available: {sorted(AGENT_REGISTRY.keys())}"
        )
    module_path = AGENT_REGISTRY[agent_name]
    return importlib.import_module(module_path)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _build_challenge_log_dir(
    log_base: str | Path,
    agent_name: str,
    model_name: str,
    run_id: str,
    category: str,
    chal_id: str,
) -> Path:
    """Return the per-challenge log directory following the project layout.

    Layout::

        <log_base>/batch/<agent_name>/<model_name>/<timestamp>_<run_id>/challenges/<category>/<chal_id>/
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return (
        Path(log_base)
        / "batch"
        / agent_name
        / model_name
        / f"{timestamp}_{run_id}"
        / "challenges"
        / category
        / chal_id
    )


def _setup_challenge_logger(
    log_dir: Path,
    chal_id: str,
    log_level: str = "DEBUG",
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    console_output: bool = True,
) -> logging.Logger:
    """Create a per-challenge logger that writes to *agent.log* and optionally
    to the console.  The logger does not propagate to ancestor loggers to avoid
    duplicate output.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent.log"

    chal_logger = logging.getLogger(f"baseline.runner.{chal_id}.{uuid.uuid4().hex[:6]}")
    chal_logger.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
    chal_logger.propagate = False
    chal_logger.handlers.clear()

    formatter = logging.Formatter(log_format)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
    fh.setFormatter(formatter)
    chal_logger.addHandler(fh)

    # Console handler (optional)
    if console_output:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        ch.setFormatter(formatter)
        chal_logger.addHandler(ch)

    return chal_logger


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------

def _save_result(log_dir: Path, result: Dict[str, Any]) -> Path:
    """Write *result* dict as ``result.json`` inside *log_dir* and return
    the path to the file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    result_path = log_dir / "result.json"
    with result_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    return result_path


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_single_challenge(
    *,
    agent_name: str,
    config: Dict[str, Any],
    model_config: Dict[str, Any],
    chal_id: str,
    run_id: Optional[str] = None,
    llm_runtime: Optional[LLMDispatcherRuntime] = None,
) -> Dict[str, Any]:
    """Execute a single challenge for one baseline mini_cyberagent.

    Args:
        agent_name: Short name registered in :data:`AGENT_REGISTRY`.
        config: Full config dict (loaded from agent YAML).  Expected top-level
            keys: ``agent``, ``docker_environment``, ``challenge_client``, ``logging``.
        model_config: Model-specific kwargs (loaded from ``common/configs/model.yml``).
            Must contain ``model``, ``openai_api_base``, ``openai_api_key``, etc.
        chal_id: Challenge identifier understood by ``ChallengeClient``.
        run_id: Optional run identifier (auto-generated if omitted).
        llm_runtime: Optional pre-started ``LLMDispatcherRuntime``.  If not
            provided a short-lived one is created and shut down within this
            function.

    Returns:
        Result dict with keys: ``solved``, ``steps_completed``,
        ``elapsed_seconds``, ``tokens_total``, ``error``, plus
        ``agent_name``, ``chal_id``, ``model_name``, ``log_dir``.
    """
    run_id = run_id or uuid.uuid4().hex[:8]
    model_name = model_config.get("model", "unknown")
    start_time = time.time()
    log_dir: Optional[Path] = None  # Set after ChallengeClient provides category

    # -- Resolve agent module -------------------------------------------
    try:
        agent_module = get_agent_module(agent_name)
    except (KeyError, ImportError) as exc:
        logger.error("Failed to load agent module for '%s': %s", agent_name, exc)
        return _error_result(agent_name, chal_id, model_name, exc, start_time)

    # -- Derived config slices ------------------------------------------
    agent_cfg = config.get("agent", {})
    docker_cfg = config.get("docker_environment", {})
    client_cfg = config.get("challenge_client", {})
    logging_cfg = config.get("logging", {})

    step_limit = agent_cfg.get("step_limit", 10)
    model_kwargs = {**agent_cfg.get("model_kwargs", {}), **model_config}
    # Per-agent extra kwargs forwarded verbatim into run_challenge(**...).
    # Keeps agent-specific knobs (e.g. vulnbot's max_interactions) out of the
    # generic runner/worker schemas while still being yaml-configurable.
    extra_agent_kwargs = dict(agent_cfg.get("agent_kwargs", {}) or {})

    # -- Per-challenge logging ------------------------------------------
    log_base = logging_cfg.get("log_dir", "baseline/logs")
    log_level = logging_cfg.get("level", "DEBUG")
    log_format = logging_cfg.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_output = logging_cfg.get("console_output", True)

    # Use a temporary logger first; will be re-setup after ChallengeClient gives us the real category
    chal_logger = logging.getLogger(f"baseline.runner.{chal_id}.{uuid.uuid4().hex[:6]}")
    chal_logger.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
    chal_logger.propagate = False
    chal_logger.handlers.clear()
    if console_output:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(log_format))
        chal_logger.addHandler(ch)

    chal_logger.info(
        "Starting challenge %s with agent=%s model=%s run_id=%s",
        chal_id, agent_name, model_name, run_id,
    )

    # -- Resources that need cleanup ------------------------------------
    challenge_client: Optional[ChallengeClient] = None
    docker_env: Optional[DockerEnvironment] = None
    own_llm_runtime = False

    if llm_runtime is None:
        try:
            mp_ctx = multiprocessing.get_context("spawn")
            llm_runtime = LLMDispatcherRuntime(mp_context=mp_ctx)
            llm_runtime.start()
            own_llm_runtime = True
        except Exception as exc:
            chal_logger.error("Failed to start LLMDispatcherRuntime: %s", exc)
            return _error_result(agent_name, chal_id, model_name, exc, start_time, log_dir)

    try:
        # -- ChallengeClient --------------------------------------------------
        chal_logger.info("Initializing ChallengeClient (run_mode=%s)", client_cfg.get("run_mode", "remote"))
        client_config = ChallengeClientConfig(**client_cfg)
        challenge_client = ChallengeClient(config=client_config, logger=chal_logger)

        if chal_id not in challenge_client.challenges:
            available = sorted(challenge_client.challenges.keys())[:20]
            chal_logger.error(
                "Challenge '%s' not found. First 20 available: %s",
                chal_id, available,
            )
            return _error_result(
                agent_name, chal_id, model_name,
                ValueError(f"Challenge '{chal_id}' not found"),
                start_time, log_dir,
            )

        # Get challenge data with per-agent target isolation
        runtime_args = runtime_args_for_agent(
            agent_name,
            challenge_client.challenges.get(chal_id, {}),
        )
        chal_data = challenge_client.get_challenge_data(
            chal_id, runtime_args=runtime_args,
        )

        # Now that we have the real category, set up proper file logging
        real_category = chal_data.get("category", "unknown")
        log_dir = _build_challenge_log_dir(
            log_base, agent_name, model_name, run_id,
            real_category, chal_id,
        )
        file_logger = _setup_challenge_logger(
            log_dir, chal_id,
            log_level=log_level,
            log_format=log_format,
            console_output=console_output,
        )
        # Transfer to the file-capable logger
        chal_logger = file_logger

        chal_logger.info(
            "Challenge data loaded: category=%s benchmark=%s target_status=%s",
            chal_data.get("category"),
            chal_data.get("benchmark"),
            chal_data.get("target_status"),
        )

        # -- DockerEnvironment -------------------------------------------
        # Connect to the target's network if the challenge is dynamic.
        resolved_docker_cfg = docker_config_for_challenge(docker_cfg, chal_data)
        docker_network = resolved_docker_cfg.get("network_name") or "ctfnet"

        docker_env_config = DockerEnvironmentConfig(
            image=resolved_docker_cfg.get("image", "ctfenv"),
            network_name=docker_network,
            timeout=resolved_docker_cfg.get("timeout", 30),
            cwd=resolved_docker_cfg.get("cwd", "/"),
        )
        chal_logger.info(
            "Creating DockerEnvironment image=%s network=%s",
            docker_env_config.image, docker_env_config.network_name,
        )
        docker_env = DockerEnvironment(config=docker_env_config, logger=chal_logger)

        # -- Prepare challenge files in container ------------------------
        chal_dir_name = docker_env._prepare_challenge_files(chal_data)
        chal_data["workspace"] = f"/ctf/{chal_dir_name}"
        chal_logger.info("Workspace set to: %s", chal_data["workspace"])

        # -- LLMClientStub -----------------------------------------------
        chal_logger.info("Building LLM client for model=%s", model_name)
        llm_stub = llm_runtime.handle.build_client(model_kwargs)

        # -- Run the agent -----------------------------------------------
        chal_logger.info(
            "Calling %s.run_challenge() step_limit=%d", agent_name, step_limit,
        )
        result = agent_module.run_challenge(
            chal_data=chal_data,
            docker_env=docker_env,
            llm_stub=llm_stub,
            logger_instance=chal_logger,
            step_limit=step_limit,
            model_kwargs=model_kwargs,
            model=model_name,
            **extra_agent_kwargs,
        )

        # -- Post-run bookkeeping ----------------------------------------
        elapsed = time.time() - start_time
        result.setdefault("elapsed_seconds", round(elapsed, 1))
        result["agent_name"] = agent_name
        result["chal_id"] = chal_id
        result["model_name"] = model_name
        result["run_id"] = run_id
        result["log_dir"] = str(log_dir)

        result_path = _save_result(log_dir, result)
        chal_logger.info(
            "Challenge finished: solved=%s steps=%d elapsed=%.1fs result=%s",
            result.get("solved"),
            result.get("steps_completed", 0),
            result.get("elapsed_seconds", 0),
            result_path,
        )
        return result

    except Exception as exc:
        chal_logger.exception("Unhandled error in run_single_challenge")
        return _error_result(agent_name, chal_id, model_name, exc, start_time, log_dir)

    finally:
        # -- Cleanup: DockerEnvironment ----------------------------------
        if docker_env is not None:
            try:
                chal_logger.info("Cleaning up DockerEnvironment")
                docker_env.cleanup()
            except Exception as exc:
                chal_logger.warning("DockerEnvironment cleanup failed: %s", exc)

        # -- Cleanup: ChallengeClient -----------------------------------------
        if challenge_client is not None:
            try:
                chal_logger.info("Tearing down ChallengeClient for %s", chal_id)
                challenge_client.teardown(chal_id)
            except Exception as exc:
                chal_logger.warning("ChallengeClient teardown failed: %s", exc)

        # -- Cleanup: LLMDispatcherRuntime (only if we own it) -----------
        if own_llm_runtime and llm_runtime is not None:
            try:
                chal_logger.info("Shutting down LLMDispatcherRuntime")
                llm_runtime.shutdown()
            except Exception as exc:
                chal_logger.warning("LLMDispatcherRuntime shutdown failed: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_result(
    agent_name: str,
    chal_id: str,
    model_name: str,
    error: BaseException,
    start_time: float,
    log_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a standardised error result dict."""
    elapsed = time.time() - start_time
    return {
        "solved": False,
        "steps_completed": 0,
        "elapsed_seconds": round(elapsed, 1),
        "tokens_total": 0,
        "error": f"{type(error).__name__}: {error}",
        "agent_name": agent_name,
        "chal_id": chal_id,
        "model_name": model_name,
        "log_dir": str(log_dir) if log_dir else "",
    }
