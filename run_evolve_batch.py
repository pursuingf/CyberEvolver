#!/usr/bin/env python3
from __future__ import annotations

"""
Evolutionary CTF Agent — Main Entry Point
"""
import os
import sys
import json
import yaml
import time
import logging
import argparse
import traceback
import shutil
import uuid
import psutil
import signal
import multiprocessing
import inspect
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, MutableMapping
from functools import partial
from collections import defaultdict
from dataclasses import asdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool

from common.agent_runtime.docker_env import DockerEnvironment
from common.agent_runtime.docker_manager import GlobalDockerManager
from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig
from common.utils.worker_diagnostics import format_worker_phase_message
from common.utils.process_pool_guards import (
    close_task_log_handler,
    handle_global_dispatcher_fatal_outage,
    record_global_broken_pool_results,
)
from common.utils.safe_logging import safe_format_exception, safe_log_exception, safe_log_message
from common.utils.container_paths import opaque_token, sanitize_container_path_token
from common.utils.runtime_policy import normalize_target_scope, resolve_target_scope, should_auto_init_target
from common.utils.target_runtime import ChallengeRuntimeCoordinator
from run_evolve.config_loader import (
    EVO_CONFIG,
    EVO_NO_BEAM_CONFIG,
    RAW_CONFIG,
    get_model_configs,
    load_global_config,
    prepare_model_kwargs_for_dispatch,
    resolve_execution_config,
)
from run_evolve.cli import get_target_challenges, parse_args
from run_evolve.node_task import run_node_task
from run_evolve.evolution_loop import EvolutionLoop
from run_evolve.single_challenge import evolve_single_challenge
from run_evolve.dispatcher_helpers import (
    get_dispatcher_fatal_snapshot,
    stop_for_active_dispatcher_fatal,
    sync_agent_runtime_network,
)
from run_evolve.scheduling import (
    build_pending_challenge_items,
    fill_available_challenge_slots,
    format_category_mix,
    format_scheduler_category_progress,
)
from run_evolve.runtime_args import (
    apply_prompt_variant_override,
    filter_challenge_client_runtime_args,
    load_challenge_data_for_submission,
    resolve_benchmark_runtime_args,
    resolve_challenge_client_runtime_args,
)
from run_evolve.lifecycle import (
    cleanup_inflight_challenges,
    cleanup_on_interrupt,
    finish_challenge_with_logging,
    get_challenge_logger,
    kill_all_descendants,
    setup_logger,
    setup_run_directory,
    signal_handler,
    sigterm_handler,
)


# ==============================================================================
# Main Function
# ==============================================================================

def main():
    from common.llm_dispatch.dispatcher import LLMDispatcherRuntime
    from common.utils.llm_usage import FileTokenBudget

    args = parse_args()

    # --- Config Loading ---
    global_config = load_global_config(args.config)
    run_dir = setup_run_directory(args)
    global_log_file = run_dir / "run.log"
    global_logger = setup_logger("global", global_log_file, console=True)
    global_logger.info("📁 Global run root: %s", run_dir.resolve())
    global_logger.info(
        "Token budget: max_total_tokens=%s, max_chal_tokens=%s, enforce=%s",
        getattr(args, "max_total_tokens", None),
        getattr(args, "max_chal_tokens", None),
        getattr(args, "enforce_token_budget", False),
    )

    # Initialize shared token budget file (cross-process)
    budget_path = run_dir / "token_budget.json"
    budget = FileTokenBudget(
        budget_path,
        max_total_tokens=getattr(args, "max_total_tokens", None),
        max_chal_tokens=getattr(args, "max_chal_tokens", None),
    )
    # Ensure file exists early (before spawning workers)
    if not budget_path.exists():
        budget_path.write_text(json.dumps(budget.read_snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    model_configs = get_model_configs()
    if args.model and args.model not in model_configs:
        global_logger.error(f"❌ Model '{args.model}' not found in common/configs/model.yml")
        sys.exit(1)
    execution_config = resolve_execution_config(args.config_mode, model_name=args.model)


    # --- Challenge Selection ---
    challenge_client = ChallengeClient(config=ChallengeClientConfig(**global_config["challenge_client"],server_url=args.challenge_server_url), logger=global_logger)
    try:
        target_chals_meta = get_target_challenges(challenge_client, args)
    except ValueError as e:
        global_logger.error(str(e))
        sys.exit(1)

    meta = {
        "args": vars(args),
        "evo_config": execution_config.copy(),
        "challenges": list(target_chals_meta.keys()),
        "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    global_logger.info("🚀 Evolution run started")
    global_logger.info("Args: %s", vars(args))
    if args.ids:
        desc = f"IDs: {args.ids}"
    elif args.challenge_id:
        desc = f"Single ID: {args.challenge_id}"
    elif args.benchmark and args.category:
        desc = f"Benchmark '{args.benchmark}' + Categories '{args.category}'"
    elif args.benchmark:
        desc = f"Benchmark '{args.benchmark}'"
    elif args.category:
        desc = f"Categories '{args.category}'"
    else:
        desc = "All challenges"

    global_logger.info("Challenges: %s", desc)
    global_logger.info("Expanded list: %s", list(target_chals_meta.keys()))
    if args.prompt_variant:
        global_logger.info("Prompt variant override: %s", args.prompt_variant)

    chal_llm_kwargs = prepare_model_kwargs_for_dispatch(model_configs[execution_config["base_model"]])
    mut_llm_kwargs = prepare_model_kwargs_for_dispatch(model_configs[execution_config["mutation_model"]])

    pending_items = build_pending_challenge_items(target_chals_meta)
    global_logger.info("Execution mix by category: %s", format_category_mix(pending_items))
    global_logger.info("Scheduling mode: global rolling queue (throughput-first, lazy challenge init)")

    results = []
    executor = None
    dispatcher_runtime = None
    inflight_futures: Dict[Any, Dict[str, Any]] = {}
    try:
        # Determine overall max workers. We reuse the same pool.
        total_challenges = len(target_chals_meta)
        max_workers = args.max_workers if total_challenges > 0 else 1

        mp_context = multiprocessing.get_context('spawn')
        dispatcher_runtime = LLMDispatcherRuntime(
            mp_context=mp_context,
            max_inflight=args.llm_max_inflight,
            max_inflight_per_lane=args.llm_max_inflight_per_lane,
            default_timeout_s=args.llm_request_timeout,
            default_max_attempts=args.llm_max_attempts,
            response_timeout_s=args.llm_response_timeout,
            metrics_path=run_dir / "dispatcher_metrics.jsonl",
            summary_log_path=global_log_file,
            fatal_window_seconds=args.llm_fatal_window_seconds,
            fatal_non200_threshold=args.llm_fatal_non200_threshold,
            fatal_total_fail_threshold=args.llm_fatal_total_fail_threshold,
            fatal_min_success=args.llm_fatal_min_success,
            fatal_consecutive_fails=args.llm_fatal_consecutive_fails,
            fatal_fail_rate_threshold=args.llm_fatal_fail_rate_threshold,
            fatal_fail_rate_min_samples=args.llm_fatal_fail_rate_min_samples,
            disable_fatal_breaker=args.llm_disable_fatal_breaker,
            large_request_threshold=args.llm_large_request_threshold,
            large_request_delay_s=args.llm_large_request_delay,
        )
        dispatcher_runtime.start()
        global_logger.info(
            "LLM dispatcher started: max_inflight=%d, per_lane=%d, request_timeout=%.1fs, max_attempts=%d, large_threshold=%d, large_delay=%.1fs",
            args.llm_max_inflight,
            args.llm_max_inflight_per_lane,
            args.llm_request_timeout,
            args.llm_max_attempts,
            args.llm_large_request_threshold,
            args.llm_large_request_delay,
        )
        global_logger.info("LLM dispatcher metrics: %s", (run_dir / "dispatcher_metrics.jsonl").resolve())
        executor = ProcessPoolExecutor(max_workers=max_workers, mp_context=mp_context)

        executor_broken = False
        dispatcher_fatal = False

        def submit_challenge(item: Dict[str, Any], specific_chal_data: Dict[str, Any]):
            chal_id = item["chal_id"]
            global_logger.info(
                "🚀 submit chal=%s category=%s inflight=%d pending=%d",
                chal_id,
                item.get("category", "unknown"),
                len(inflight_futures) + 1,
                len(pending_items),
            )
            return executor.submit(
                evolve_single_challenge,
                chal_id=chal_id,
                chal_data=specific_chal_data,
                run_dir=run_dir,
                global_config=global_config,
                evo_config=execution_config,
                args=args,
                dispatcher_handle=dispatcher_runtime.handle,
                base_llm_kwargs=chal_llm_kwargs,
                mutation_llm_kwargs=mut_llm_kwargs,
            )

        def handle_submit_error(item: Dict[str, Any], error: Exception):
            chal_id = item["chal_id"]
            category = item.get("category", "unknown")
            safe_log_exception(
                global_logger,
                f"❌ {chal_id} | Challenge data init failed",
                exc=error,
            )
            results.append({
                "chal_id": chal_id,
                "category": category,
                "status": "failed",
                "error": f"challenge data init failed: {error}",
                "traceback": safe_format_exception(error),
            })
            finish_challenge_with_logging(
                challenge_client=challenge_client,
                chal_id=chal_id,
                logger=global_logger,
            )

        fill_available_challenge_slots(
            pending_items=pending_items,
            inflight_futures=inflight_futures,
            max_workers=max_workers,
            load_challenge_data=challenge_client.get_challenge_data,
            submit_challenge=submit_challenge,
            on_submit_error=handle_submit_error,
            resolve_runtime_args=lambda item: resolve_challenge_client_runtime_args(
                global_config,
                item.get("chal_meta", {}),
            ),
            prompt_variant=args.prompt_variant,
        )

        while inflight_futures:
            done_futures, _ = wait(tuple(inflight_futures.keys()), return_when=FIRST_COMPLETED)

            for future in done_futures:
                context = inflight_futures.pop(future)
                chal_id = context["chal_id"]
                category = context.get("category", "unknown")

                try:
                    res = future.result()
                    results.append(res)
                    status = res.get("status", "unknown")
                    sr = res.get("best_success_rate", 0.0)
                    global_logger.info(
                        "✅ %s | Status: %s | Best SR: %.1f%% | Progress: %d/%d done, %d inflight, %d pending",
                        chal_id,
                        status,
                        sr * 100,
                        len(results),
                        total_challenges,
                        len(inflight_futures),
                        len(pending_items),
                    )
                    try:
                        snap = budget.read_snapshot()
                        by = snap.get("by_challenge", {}).get(chal_id, {})
                        used_chal = int(by.get("total_tokens", 0) or 0)
                        used_chal_in = int(by.get("input_tokens", 0) or 0)
                        used_chal_out = int(by.get("output_tokens", 0) or 0)
                        max_chal = snap.get("max_chal_tokens")
                        chal_msg = f"{used_chal}" + (f"/{int(max_chal)}" if max_chal is not None else "")
                        global_logger.info(
                            "[BUDGET] chal=%s total=%s (in=%d out=%d)",
                            chal_id,
                            chal_msg,
                            used_chal_in,
                            used_chal_out,
                        )
                    except Exception as budget_error:
                        global_logger.warning("[BUDGET] per-challenge snapshot failed (%s): %s", chal_id, budget_error)

                    finish_challenge_with_logging(
                        challenge_client=challenge_client,
                        chal_id=chal_id,
                        logger=global_logger,
                    )

                    if res.get("fatal_outage"):
                        dispatcher_fatal = True
                        fatal_message = str(res.get("error") or "dispatcher fatal outage detected")
                        global_logger.error("🛑 LLM dispatcher fatal outage detected. Stopping the run early.")
                        fatal_counts = handle_global_dispatcher_fatal_outage(
                            inflight_contexts=inflight_futures,
                            pending_items=pending_items,
                            error_message=fatal_message,
                            results=results,
                            global_logger=global_logger,
                            executor=executor,
                        )
                        global_logger.error(
                            "completed_before_outage=%d inflight_failed_due_to_outage=%d pending_failed_before_submission=%d",
                            fatal_counts["completed_before_outage"],
                            fatal_counts["inflight_failed_due_to_outage"],
                            fatal_counts["pending_failed_before_submission"],
                        )
                        break

                    fatal_counts = stop_for_active_dispatcher_fatal(
                        dispatcher_handle=dispatcher_runtime.handle if dispatcher_runtime is not None else None,
                        inflight_futures=inflight_futures,
                        pending_items=pending_items,
                        results=results,
                        global_logger=global_logger,
                        executor=executor,
                    )
                    if fatal_counts is not None:
                        dispatcher_fatal = True
                        break

                    global_logger.info(
                        "📊 Category progress | %s",
                        format_scheduler_category_progress(results, inflight_futures, pending_items),
                    )
                except BrokenProcessPool as error:
                    executor_broken = True
                    safe_log_exception(
                        global_logger,
                        f"❌ {chal_id} | Process pool broke during result collection",
                        exc=error,
                    )
                    record_global_broken_pool_results(
                        failed_context=context,
                        inflight_contexts=inflight_futures,
                        pending_items=pending_items,
                        error=error,
                        results=results,
                        global_logger=global_logger,
                    )
                    if executor is not None:
                        try:
                            executor.shutdown(wait=False, cancel_futures=True)
                        except Exception:
                            pass
                    inflight_futures.clear()
                    pending_items.clear()
                    break
                except Exception as error:
                    safe_log_exception(
                        global_logger,
                        f"❌ {chal_id} | Exception during evolution",
                        exc=error,
                    )
                    results.append({
                        "chal_id": chal_id,
                        "category": category,
                        "status": "failed",
                        "error": str(error),
                        "traceback": safe_format_exception(error),
                    })
                    finish_challenge_with_logging(
                        challenge_client=challenge_client,
                        chal_id=chal_id,
                        logger=global_logger,
                    )

                if executor_broken:
                    break
                if dispatcher_fatal:
                    break

                fill_available_challenge_slots(
                    pending_items=pending_items,
                    inflight_futures=inflight_futures,
                    max_workers=max_workers,
                    load_challenge_data=challenge_client.get_challenge_data,
                    submit_challenge=submit_challenge,
                    on_submit_error=handle_submit_error,
                    resolve_runtime_args=lambda item: resolve_challenge_client_runtime_args(
                        global_config,
                        item.get("chal_meta", {}),
                    ),
                    prompt_variant=args.prompt_variant,
                )

            if executor_broken:
                global_logger.error(
                    "🛑 Process pool broke during global scheduling. Stopping remaining submissions."
                )
                break
            if dispatcher_fatal:
                break

    except KeyboardInterrupt:
        global_logger.warning("🛑 Main process interrupted! Shutting down process pool...")
        cleaned_challenges = cleanup_on_interrupt(
            executor=executor,
            challenge_client=challenge_client,
            inflight_futures=inflight_futures,
            global_logger=global_logger,
        )
        if cleaned_challenges:
            global_logger.warning(
                "🧹 Cleanup requested for inflight challenges before exit: %s",
                cleaned_challenges,
            )
        sys.exit(130)

    finally:
        # Always shutdown executor
        if executor:
            executor.shutdown(wait=True)
        if dispatcher_runtime:
            dispatcher_runtime.shutdown()
        try:
            challenge_client.close()
        except Exception:
            pass
        global_logger.info("ProcessGroup | Ensuring all subprocesses are dead...")

    # --- 📊 Final Summary ---
    summary = {
        "total_challenges": len(results),
        "by_category": defaultdict(lambda: {"total": 0, "solved": 0, "early_stop": 0}),
        "overall_success_rate": 0.0,
        "overall_input_tokens": 0,
        "overall_output_tokens": 0,
        "overall_total_tokens": 0,
        "max_total_tokens": getattr(args, "max_total_tokens", None),
        "max_chal_tokens": getattr(args, "max_chal_tokens", None),
        "details": []
    }

    # Attach final budget snapshot (cross-process aggregate)
    try:
        snap = budget.read_snapshot()
        used = snap.get("used", {}) or {}
        summary["overall_input_tokens"] = int(used.get("input_tokens", 0) or 0)
        summary["overall_output_tokens"] = int(used.get("output_tokens", 0) or 0)
        summary["overall_total_tokens"] = int(used.get("total_tokens", 0) or 0)
        # Prefer persisted maxima if present
        if snap.get("max_total_tokens") is not None:
            summary["max_total_tokens"] = snap.get("max_total_tokens")
        if snap.get("max_chal_tokens") is not None:
            summary["max_chal_tokens"] = snap.get("max_chal_tokens")
        budget_by_chal = snap.get("by_challenge", {}) or {}
    except Exception:
        budget_by_chal = {}

    for res in results:
        cat = res.get("category", "unknown")
        sr = res.get("best_success_rate", 0.0)
        status = res["status"]
        early = res.get("early_stop_at") is not None

        summary["by_category"][cat]["total"] += 1
        if sr >= 0.3:
            summary["by_category"][cat]["solved"] += 1
        if early:
            summary["by_category"][cat]["early_stop"] += 1

        detail = {
            "chal_id": res["chal_id"],
            "category": cat,
            "status": status,
            "fatal_outage": bool(res.get("fatal_outage", False)),
            "best_success_rate": sr,
            "early_stop_at": res.get("early_stop_at"),
            "best_node_id": res.get("best_node_id"),
            "input_tokens": int((budget_by_chal.get(res["chal_id"], {}) or {}).get("input_tokens", 0) or 0),
            "output_tokens": int((budget_by_chal.get(res["chal_id"], {}) or {}).get("output_tokens", 0) or 0),
            "total_tokens": int((budget_by_chal.get(res["chal_id"], {}) or {}).get("total_tokens", 0) or 0),
        }
        # Optional: add error if failed
        if status == "failed":
            detail["error"] = res.get("error")
        summary["details"].append(detail)

    # Overall solved ratio
    solved = sum(stats["solved"] for stats in summary["by_category"].values())
    summary["overall_success_rate"] = solved / len(results) if results else 0.0

    # Write summary
    summary_path = run_dir / "evolution_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print summary to log
    global_logger.info("=" * 60)
    global_logger.info("🎯 EVOLUTION SUMMARY")
    global_logger.info("=" * 60)
    global_logger.info("Total Challenges: %d", summary["total_challenges"])
    global_logger.info("Solved (SR ≥ 30%%): %d/%d (%.1f%%)",
                       solved, len(results), summary["overall_success_rate"] * 100)
    global_logger.info(
        "Token usage (overall): total=%d (in=%d out=%d)",
        summary["overall_total_tokens"],
        summary["overall_input_tokens"],
        summary["overall_output_tokens"],
    )

    for cat, stats in summary["by_category"].items():
        solved_ratio = stats["solved"] / stats["total"] if stats["total"] > 0 else 0
        global_logger.info("  [%s] %d/%d solved (%.0f%%), early-stop: %d",
                           cat, stats["solved"], stats["total"], solved_ratio * 100, stats["early_stop"])

    global_logger.info("📄 Full report saved to: %s", summary_path.resolve())
    global_logger.info("👋 Evolution run completed.")


if __name__ == "__main__":
    main()
