"""Per-challenge evolution driver: configures dispatcher-backed LLMs, runs the loop."""
from __future__ import annotations

import argparse
import logging
import os
import signal
from functools import partial
from pathlib import Path
from typing import Any, Dict

from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig
from common.agent_runtime.docker_manager import GlobalDockerManager
from common.llm_dispatch.dispatcher import LLMDispatcherHandle
from common.utils.safe_logging import safe_format_exception, safe_log_exception, safe_log_message
from common.utils.target_runtime import ChallengeRuntimeCoordinator
from common.utils.worker_diagnostics import format_worker_phase_message

from run_evolve.evolution_loop import EvolutionLoop
from run_evolve.lifecycle import finish_challenge_with_logging, get_challenge_logger, sigterm_handler
from run_evolve.node_task import run_node_task
from run_evolve.runtime_args import filter_challenge_client_runtime_args, resolve_benchmark_runtime_args


def evolve_single_challenge(
    chal_id: str,
    chal_data,
    run_dir: Path,
    global_config: Dict[str, Any],
    evo_config: Dict[str, Any],
    args: argparse.Namespace,
    dispatcher_handle: LLMDispatcherHandle,
    base_llm_kwargs: Dict,
    mutation_llm_kwargs: Dict,
) -> Dict[str, Any]:
    from cyber_evolver.evolve.orchestrator import EvolutionOrchestrator
    from cyber_evolver.evolve.scheduler import TaskScheduler
    from cyber_evolver.evolve.selector import TopKSelector
    from common.llm_dispatch.dispatcher import LLMDispatcherFatalError
    from common.utils.llm_usage import (
        FileTokenBudget,
        InstrumentedLLM,
        JSONLUsageLogger,
        TokenBudgetExceededError,
    )

    if chal_data.get("target_status", "") == "stopped":
        error_msg = f"Challenge {chal_id} is already in 'stopped' state before start."
        print(f"❌ [Pre-Check] {error_msg}")
        return {
            "chal_id": chal_id,
            "category": chal_data.get("category", "unknown"),
            "status": "aborted_target_stopped",
            "error": error_msg,
            "best_success_rate": 0.0
        }

    signal.signal(signal.SIGTERM, sigterm_handler)
    base_seed_path = args.base_seed_path
    category = chal_data.get("category", "unknown")
    chal_run_dir = run_dir / category / chal_id
    chal_run_dir.mkdir(parents=True, exist_ok=True)
    worker_phase = "startup"

    # --- Logging ---
    chal_logger = get_challenge_logger(chal_id, category, run_dir)
    chal_logger.info("🚀 Starting evolution for challenge: %s (%s)", chal_id, category)
    chal_logger.info(format_worker_phase_message(chal_id, os.getpid(), worker_phase))

    # --- Docker ---
    benchmark_runtime_args = resolve_benchmark_runtime_args(global_config, chal_data)
    docker_manager = GlobalDockerManager(
        global_config["docker_environment"],
        chal_id=chal_id,
        logger=chal_logger,
        default_runtime_args=benchmark_runtime_args,
    )
    chal_logger.info("Caching %s...", chal_id)
    docker_manager.prepare_challenge_cache(chal_id, chal_data)
    runtime_challenge_client = None
    try:
        challenge_runtime_args = filter_challenge_client_runtime_args(benchmark_runtime_args)
        runtime_challenge_client = ChallengeClient(
            config=ChallengeClientConfig(**global_config["challenge_client"], server_url=args.challenge_server_url),
            logger=chal_logger,
        )
        if challenge_runtime_args:
            runtime_challenge_client.remember_runtime_args(chal_id, challenge_runtime_args)
        runtime_coordinator = ChallengeRuntimeCoordinator(
            challenge_client=runtime_challenge_client,
            challenge_id=chal_id,
            logger=chal_logger,
        )
        if hasattr(docker_manager, "set_runtime_coordinator"):
            docker_manager.set_runtime_coordinator(runtime_coordinator)
        else:
            docker_manager.runtime_coordinator = runtime_coordinator
            if getattr(docker_manager, "env", None) is not None:
                docker_manager.env.runtime_coordinator = runtime_coordinator

        worker_phase = "llm_setup"
        chal_logger.info(format_worker_phase_message(chal_id, os.getpid(), worker_phase))

        usage_logger = JSONLUsageLogger(chal_run_dir / "llm_usage.jsonl")
        token_budget = FileTokenBudget(
            run_dir / "token_budget.json",
            max_total_tokens=getattr(args, "max_total_tokens", None),
            max_chal_tokens=getattr(args, "max_chal_tokens", None),
        )

        base_llm = InstrumentedLLM(
            dispatcher_handle.build_client(
                base_llm_kwargs,
                timeout=getattr(args, "llm_request_timeout", 300.0),
            ),
            usage_logger=usage_logger,
            base_meta={
                "llm_role": "base",
                "component": "agent",
                "chal_id": chal_id,
                "model": base_llm_kwargs.get("model"),
            },
            budget=token_budget,
            enforce_budget=getattr(args, "enforce_token_budget", False),
        )
        mutation_llm = InstrumentedLLM(
            dispatcher_handle.build_client(
                mutation_llm_kwargs,
                timeout=getattr(args, "llm_request_timeout", 300.0),
            ),
            usage_logger=usage_logger,
            base_meta={
                "llm_role": "mutation",
                "component": "evolve",
                "chal_id": chal_id,
                "model": mutation_llm_kwargs.get("model"),
            },
            budget=token_budget,
            enforce_budget=getattr(args, "enforce_token_budget", False),
        )

        # --- Orchestrator ---
        worker_phase = "orchestrator_setup"
        chal_logger.info(format_worker_phase_message(chal_id, os.getpid(), worker_phase))
        orchestrator = EvolutionOrchestrator(
            root_dir=str(chal_run_dir),
            base_seed_path=base_seed_path,
            seed_includes=list(getattr(args, "seed_include", []) or []),
            llm=mutation_llm,
            logger=chal_logger,
            prompt_cfg_path=args.evolve_prompt_cfg,
            docker_manager=docker_manager,
            ablation_mode=getattr(args, "ablation", "none"),
        )

        # --- Scheduler ---
        worker_phase = "scheduler_setup"
        chal_logger.info(format_worker_phase_message(chal_id, os.getpid(), worker_phase))
        task_fn = partial(
            run_node_task,
            llm=base_llm,
            max_steps=global_config["agent"]["step_limit"],
            docker_manager=docker_manager,
            logger_level=logging.INFO
        )
        scheduler = TaskScheduler(task_fn=task_fn, max_workers=args.task_workers, logger=chal_logger)

        # --- Selector ---
        selector = TopKSelector(primary_key="success_rate", secondary_key="assessment_score")

        # --- Evolution Loop ---
        worker_phase = "evolution_loop"
        chal_logger.info(
            format_worker_phase_message(
                chal_id,
                os.getpid(),
                worker_phase,
                detail="about to call loop.run()",
            )
        )
        loop = EvolutionLoop(
            orchestrator=orchestrator,
            scheduler=scheduler,
            selector=selector,
            evo_config=evo_config,
            chal_id=chal_id,
            chal_data=chal_data,
            chal_logger=chal_logger,
            success_threshold=0.3
        )

        result = loop.run()
        worker_phase = "result_ready"
        chal_logger.info(
            format_worker_phase_message(
                chal_id,
                os.getpid(),
                worker_phase,
                detail=f"loop.run() returned status={result.get('status')} early_stop_at={result.get('early_stop_at')}",
            )
        )
        if getattr(args, "enforce_token_budget", False) and token_budget.exceeded(chal_id=chal_id):
            result["status"] = "aborted_budget"
            result["error"] = "Token budget exceeded"
        try:
            snap = token_budget.read_snapshot()
            result["token_usage"] = (snap.get("by_challenge", {}) or {}).get(chal_id, {})
        except Exception as snapshot_error:
            chal_logger.debug("Token usage snapshot read failed for %s: %s", chal_id, snapshot_error)
        worker_phase = "returning_result"
        chal_logger.info(
            format_worker_phase_message(
                chal_id,
                os.getpid(),
                worker_phase,
                detail=f"returning status={result.get('status')} best_success_rate={result.get('best_success_rate')}",
            )
        )
        result["chal_id"] = chal_id
        result["category"] = category
        return result
    except TokenBudgetExceededError as e:
        worker_phase = "token_budget_exceeded"
        chal_logger.warning("🛑 Token budget exceeded for %s: %s", chal_id, e)
        chal_logger.warning(format_worker_phase_message(chal_id, os.getpid(), worker_phase, detail=str(e)))
        return {
            "chal_id": chal_id,
            "category": category,
            "status": "aborted_budget",
            "error": str(e),
        }
    except LLMDispatcherFatalError as e:
        worker_phase = "dispatcher_fatal"
        safe_log_exception(
            chal_logger,
            f"🛑 [PID:{os.getpid()}] Dispatcher fatal outage for {chal_id}",
            exc=e,
        )
        safe_log_message(
            chal_logger,
            logging.ERROR,
            format_worker_phase_message(
                chal_id,
                os.getpid(),
                worker_phase,
                detail=str(e),
            ),
        )
        return {
            "chal_id": chal_id,
            "category": category,
            "status": "failed",
            "error": str(e),
            "traceback": safe_format_exception(e),
            "fatal_outage": True,
        }
    except (KeyboardInterrupt, SystemExit):
        chal_logger.warning(f"🛑 [PID:{os.getpid()}] Interrupted by user.")
        chal_logger.warning(
            format_worker_phase_message(
                chal_id,
                os.getpid(),
                worker_phase,
                detail="worker received KeyboardInterrupt/SystemExit",
            )
        )
        return {
            "chal_id": chal_id,
            "category": category,
            "status": "interrupted",
            "error": "KeyboardInterrupt"
        }
    except Exception as e:
        worker_phase = "exception"
        safe_log_exception(
            chal_logger,
            f"💥 [PID:{os.getpid()}] Challenge {chal_id} failed with exception",
            exc=e,
        )
        safe_log_message(
            chal_logger,
            logging.ERROR,
            format_worker_phase_message(
                chal_id,
                os.getpid(),
                worker_phase,
                detail=str(e),
            ),
        )
        return {
            "chal_id": chal_id,
            "category": category,
            "status": "failed",
            "error": str(e),
            "traceback": safe_format_exception(e)
        }
    finally:
        try:
            try:
                chal_logger.info(
                    format_worker_phase_message(
                        chal_id,
                        os.getpid(),
                        worker_phase,
                        detail="cleaning sandbox",
                    )
                )
                chal_logger.info(f"🧹 [PID:{os.getpid()}] Cleaning up Docker resources for {chal_id}...")
                docker_manager.cleanup()
                chal_logger.info(f"🧹 [PID:{os.getpid()}] Docker cleanup complete for {chal_id}")
            except Exception as docker_cleanup_error:
                chal_logger.warning("Docker sandbox cleanup failed for %s: %s", chal_id, docker_cleanup_error)
            if runtime_challenge_client is not None:
                finish_challenge_with_logging(
                    challenge_client=runtime_challenge_client,
                    chal_id=chal_id,
                    logger=chal_logger,
                    pid=os.getpid(),
                )
                try:
                    runtime_challenge_client.close()
                except Exception as close_error:
                    chal_logger.warning("Close failed for %s: %s", chal_id, close_error)
            chal_logger.info(f"🧹 [PID:{os.getpid()}] Cleaning up OVER. Exit...")
        except Exception as e:
            chal_logger.warning("Cleanup failed for %s: %s", chal_id, e)
