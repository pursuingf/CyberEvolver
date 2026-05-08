"""Per-worker lifecycle module for the batch execution system.

Handles the full lifecycle for one challenge run:
1. Create ChallengeClient (per-worker, not thread-safe)
2. Get challenge data with target_scope="per_agent" for isolation
3. Create DockerEnvironment for agent container
4. Create LLMClientStub from shared LLMDispatcherHandle
5. Call agent's run_challenge()
6. Save result.json and update checkpoint.jsonl
7. Cleanup: teardown target + cleanup agent container

Callable from both ThreadPoolExecutor (Category A) and ProcessPoolExecutor
(Category B). For ProcessPoolExecutor, pass dispatcher_handle=None and a
new LLMDispatcherRuntime will be created inside the worker.
"""

from __future__ import annotations

import importlib
import json
import logging
import multiprocessing
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from baseline.agents.upstream_runner import LoggingLLMStub
from baseline.runtime_policy import docker_config_for_challenge, runtime_args_for_agent
from common.agent_runtime.challenge_client import ChallengeClientConfig, ChallengeClient
from common.agent_runtime.docker_env import DockerEnvironment, DockerEnvironmentConfig
from common.llm_dispatch.dispatcher import LLMDispatcherHandle, LLMDispatcherRuntime

logger = logging.getLogger(__name__)


def _get_agent_module(agent_name: str) -> Any:
    """Dynamically import an agent module by name.

    Agent modules live under ``baseline.agents.<agent_name>`` and must
    expose a ``run_challenge`` function.
    """
    module_path = f"baseline.agents.{agent_name}"
    return importlib.import_module(module_path)


def apply_prompt_variant_override(chal_data: Dict[str, Any], prompt_variant: str | None) -> Dict[str, Any]:
    requested_variant = str(prompt_variant or "").strip()
    if not requested_variant:
        return chal_data

    raw_variant_names = chal_data.get("variant_names")
    if not isinstance(raw_variant_names, list):
        return chal_data

    supported_variants = {str(name) for name in raw_variant_names}
    if requested_variant not in supported_variants:
        return chal_data

    updated = deepcopy(chal_data)
    updated["default_variant"] = requested_variant
    source_fields = dict(updated.get("source_fields", {}) or {})
    source_fields["default_variant"] = requested_variant
    updated["source_fields"] = source_fields
    return updated


def run_single_challenge(
    *,
    chal_id: str,
    sample_idx: int,
    agent_name: str,
    agent_config: Dict[str, Any],
    model_kwargs: Dict[str, Any],
    dispatcher_handle: Optional[LLMDispatcherHandle] = None,
    client_config: Optional[ChallengeClientConfig] = None,
    step_limit: int = 10,
    log_dir: Optional[str] = None,
    prompt_variant: Optional[str] = None,
    ace_playbook_snapshot: Optional[str] = None,
    ace_scope_key: Optional[str] = None,
    ace_playbook_version: Optional[int] = None,
    ace_disable_persist: bool = False,
) -> dict:
    """Run a single challenge through the full worker lifecycle.

    Args:
        chal_id: Challenge identifier (e.g. ``"2021q-cry-bits"``).
        sample_idx: Sample index for repeated runs of the same challenge.
        agent_name: Name of the agent module under ``baseline.agents``.
        agent_config: Full agent config dict (from YAML). Must contain
            ``challenge_client``, ``docker_environment``, and ``agent`` sub-dicts.
        model_kwargs: Model kwargs dict (merged from common/configs/model.yml + agent overrides).
        dispatcher_handle: Shared ``LLMDispatcherHandle`` for ThreadPoolExecutor.
            Pass ``None`` for ProcessPoolExecutor — a new
            ``LLMDispatcherRuntime`` will be created inside the worker.
        client_config: Pre-built ChallengeClientConfig. If None, built from agent_config.
        step_limit: Max steps for the mini_cyberagent.
        log_dir: Per-challenge directory for logs and result.json.

    Returns:
        Result dict with keys: agent, model, challenge_id, benchmark, category,
        sample_idx, solved, steps_completed, elapsed_seconds, tokens_total, error.
    """
    model_name = model_kwargs.get("model", "unknown")

    # -- Per-challenge logging setup ----------------------------------------
    if log_dir:
        log_path = Path(log_dir)
    else:
        log_path = Path(agent_config.get("logging", {}).get("log_dir", "baseline/logs")) / "batch" / agent_name / model_name / "challenges" / "unknown" / chal_id

    log_path.mkdir(parents=True, exist_ok=True)

    chal_logger = logging.getLogger(f"batch.{chal_id}.s{sample_idx}.{time.monotonic_ns()}")
    chal_logger.setLevel(logging.DEBUG)
    chal_logger.propagate = False
    chal_logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path / "agent.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    chal_logger.addHandler(file_handler)

    chal_logger.info(
        "Worker starting: agent=%s chal=%s sample=%d model=%s",
        agent_name, chal_id, sample_idx, model_name,
    )

    # -- State variables for cleanup ----------------------------------------
    challenge_client: Optional[ChallengeClient] = None
    docker_env: Optional[DockerEnvironment] = None
    llm_runtime: Optional[LLMDispatcherRuntime] = None
    logging_stub: Optional[LoggingLLMStub] = None
    solved = False
    steps_completed = 0
    elapsed_seconds = 0.0
    tokens_total = 0
    tokens_input = 0
    tokens_output = 0
    solve_tokens_total = 0
    solve_tokens_input = 0
    solve_tokens_output = 0
    reflector_tokens_total = 0
    reflector_tokens_input = 0
    reflector_tokens_output = 0
    flag: Optional[str] = None
    error: Optional[str] = None
    benchmark = ""
    category = ""
    result_log_dir = log_path  # Track actual log dir for result.json
    start_time = time.time()

    try:
        # --- 1. ChallengeClient (per-worker, not thread-safe) ---
        if client_config is not None:
            challenge_client = ChallengeClient(config=client_config, logger=chal_logger)
        else:
            client_cfg = ChallengeClientConfig(**agent_config.get("challenge_client", {}))
            challenge_client = ChallengeClient(config=client_cfg, logger=chal_logger)

        # --- 2. Get challenge data with per_agent isolation ---
        chal_meta = dict(getattr(challenge_client, "challenges", {}).get(chal_id, {}) or {})
        chal_data = challenge_client.get_challenge_data(
            chal_id,
            runtime_args=runtime_args_for_agent(agent_name, chal_meta),
        )
        chal_data = apply_prompt_variant_override(chal_data, prompt_variant)
        benchmark = chal_data.get("benchmark", "")
        category = chal_data.get("category", "")

        # Update log dir with correct category
        if category and category != "unknown":
            if log_path.name == chal_id and log_path.parent.name == category:
                result_log_dir = log_path
            elif log_path.name.startswith("iter_") and log_path.parent.name == chal_id and log_path.parent.parent.name == category:
                result_log_dir = log_path
            else:
                parent = log_path.parent.parent
                result_log_dir = parent / category / chal_id
            if result_log_dir != log_path:
                result_log_dir.mkdir(parents=True, exist_ok=True)
                new_fh = logging.FileHandler(result_log_dir / "agent.log", encoding="utf-8")
                new_fh.setLevel(logging.DEBUG)
                new_fh.setFormatter(formatter)
                chal_logger.addHandler(new_fh)

        chal_logger.info(
            "Challenge data loaded: benchmark=%s category=%s target_status=%s",
            benchmark, category, chal_data.get("target_status"),
        )
        target_status = str(chal_data.get("target_status", "") or "").lower()
        if target_status not in {"running", "static"}:
            raise RuntimeError(
                f"Target launch failed for {chal_id}: target_status={target_status or 'unknown'}"
            )

        # --- 3. DockerEnvironment for agent container ---
        docker_cfg = docker_config_for_challenge(
            agent_config.get("docker_environment", {}),
            chal_data,
        )

        docker_env_config = DockerEnvironmentConfig(**docker_cfg)
        docker_env = DockerEnvironment(config=docker_env_config, logger=chal_logger)
        chal_logger.info("Agent container started: %s", docker_env.container_id)

        # Prepare challenge files in container
        chal_dir_name = docker_env._prepare_challenge_files(chal_data)
        chal_data["workspace"] = f"/ctf/{chal_dir_name}"

        # --- 4. LLMClientStub ---
        if dispatcher_handle is not None:
            handle = dispatcher_handle
        else:
            # ProcessPoolExecutor path: create a dedicated runtime
            mp_context = multiprocessing.get_context("spawn")
            llm_runtime = LLMDispatcherRuntime(mp_context=mp_context)
            llm_runtime.start()
            handle = llm_runtime.handle
            chal_logger.info("Created per-worker LLMDispatcherRuntime")

        llm_stub = handle.build_client(model_kwargs)
        chal_logger.info("LLM stub built for model=%s", model_name)

        # Tee every LLM invoke() call into upstream.log so in-process agents
        # (vulnbot, t_agent, cy_agent, autopenbench, dcipher) produce a
        # transcript equivalent to nyuctf_single's subprocess upstream.log.
        # Subprocess-based agents keep their own upstream.log; wrapping is
        # harmless because they ignore llm_stub entirely.
        if result_log_dir is not None:
            logging_stub = LoggingLLMStub(
                llm_stub,
                Path(result_log_dir) / "upstream.log",
                label=f"{agent_name}/{chal_id}/sample={sample_idx}",
            )
            llm_stub = logging_stub

        # --- 5. Call agent's run_challenge() ---
        agent_module = _get_agent_module(agent_name)

        # Per-agent extra kwargs (e.g. vulnbot's max_interactions) come from
        # the yaml's ``agent.agent_kwargs`` block — same convention as
        # base_runner.run_challenge.  Forward verbatim into run_challenge.
        extra_agent_kwargs = dict(agent_config.get("agent", {}).get("agent_kwargs", {}) or {})

        agent_result = agent_module.run_challenge(
            chal_data=chal_data,
            docker_env=docker_env,
            llm_stub=llm_stub,
            logger_instance=chal_logger,
            model=model_name,
            model_kwargs=model_kwargs,
            step_limit=step_limit,
            log_dir=str(result_log_dir),
            ace_playbook_snapshot=ace_playbook_snapshot,
            ace_scope_key=ace_scope_key,
            ace_playbook_version=ace_playbook_version,
            ace_disable_persist=ace_disable_persist,
            **extra_agent_kwargs,
        )
        elapsed_seconds = round(time.time() - start_time, 1)

        solved = bool(agent_result.get("solved", False))
        steps_completed = int(agent_result.get("steps_completed", 0))
        tokens_total = int(agent_result.get("tokens_total", 0) or 0)
        tokens_input = int(agent_result.get("tokens_input", 0) or 0)
        tokens_output = int(agent_result.get("tokens_output", 0) or 0)
        solve_tokens_total = int(agent_result.get("solve_tokens_total", 0) or 0)
        solve_tokens_input = int(agent_result.get("solve_tokens_input", 0) or 0)
        solve_tokens_output = int(agent_result.get("solve_tokens_output", 0) or 0)
        reflector_tokens_total = int(agent_result.get("reflector_tokens_total", 0) or 0)
        reflector_tokens_input = int(agent_result.get("reflector_tokens_input", 0) or 0)
        reflector_tokens_output = int(agent_result.get("reflector_tokens_output", 0) or 0)
        flag = agent_result.get("flag")
        error = agent_result.get("error")
        chal_logger.info(
            "Agent finished: solved=%s steps=%d elapsed=%.1fs tokens=%d (in=%d out=%d)",
            solved, steps_completed, elapsed_seconds,
            tokens_total, tokens_input, tokens_output,
        )

    except Exception as exc:
        elapsed_seconds = round(time.time() - start_time, 1)
        error = str(exc)
        chal_logger.error("Worker failed: %s", exc, exc_info=True)

    finally:
        # --- Cleanup: ChallengeClient (target teardown) ---
        if challenge_client is not None:
            try:
                challenge_client.teardown(chal_id)
                chal_logger.info("Target teardown complete: %s", chal_id)
            except Exception as exc:
                chal_logger.warning("Target teardown failed: %s", exc)

        # --- Cleanup: DockerEnvironment ---
        if docker_env is not None:
            try:
                docker_env.cleanup()
                chal_logger.info("Agent container cleanup complete")
            except Exception as exc:
                chal_logger.warning("Agent container cleanup failed: %s", exc)

        # --- Cleanup: LLMDispatcherRuntime (only if we own it) ---
        if llm_runtime is not None:
            try:
                llm_runtime.shutdown()
            except Exception as exc:
                chal_logger.warning("LLMDispatcherRuntime shutdown failed: %s", exc)

        # --- Token accounting: prefer stub totals when available ---
        # LoggingLLMStub sums usage_metadata from every invoke() response, so
        # for in-process agents (vulnbot, t_agent, cy_agent, autopenbench,
        # dcipher in-process path) we always get an accurate input/output
        # split even when the agent itself doesn't propagate token counts.
        # Subprocess agents (nyuctf_single, dcipher subprocess path) bypass
        # the stub entirely and rely on the agent-reported value.
        if logging_stub is not None:
            try:
                stub_totals = logging_stub.get_token_totals()
            except Exception as exc:
                chal_logger.warning("Reading stub token totals failed: %s", exc)
                stub_totals = {}
            stub_in = int(stub_totals.get("input_tokens", 0) or 0)
            stub_out = int(stub_totals.get("output_tokens", 0) or 0)
            stub_total = int(stub_totals.get("total_tokens", 0) or 0)
            if stub_in or stub_out or stub_total:
                tokens_input = stub_in or tokens_input
                tokens_output = stub_out or tokens_output
                tokens_total = stub_total or tokens_total
        if tokens_total == 0 and (tokens_input or tokens_output):
            tokens_total = tokens_input + tokens_output

    # --- 6. Save result.json ---
    result = {
        "agent": agent_name,
        "model": model_name,
        "challenge_id": chal_id,
        "benchmark": benchmark,
        "category": category,
        "sample_idx": sample_idx,
        "solved": solved,
        "steps_completed": steps_completed,
        "elapsed_seconds": elapsed_seconds,
        "tokens_total": tokens_total,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "solve_tokens_total": solve_tokens_total,
        "solve_tokens_input": solve_tokens_input,
        "solve_tokens_output": solve_tokens_output,
        "reflector_tokens_total": reflector_tokens_total,
        "reflector_tokens_input": reflector_tokens_input,
        "reflector_tokens_output": reflector_tokens_output,
        "flag": flag,
        "error": error,
    }

    # Save to the result log directory (with correct category)
    result_dir = result_log_dir
    try:
        result_dir.mkdir(parents=True, exist_ok=True)
        with (result_dir / "result.json").open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        chal_logger.warning("Failed to save result.json: %s", exc)

    chal_logger.info("Worker done: chal=%s solved=%s", chal_id, solved)
    return result
