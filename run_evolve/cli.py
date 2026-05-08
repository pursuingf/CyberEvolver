"""argparse setup for the run_evolve_batch main entry."""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict

from common.agent_runtime.challenge_client import ChallengeClient


def get_target_challenges(challenge_client: ChallengeClient, args) -> Dict[str, Any]:
    """
    Filter challenges metadata based on arguments.
    Note: This returns METADATA only. Initialization happens in the worker thread.
    """
    all_chals = challenge_client.challenges  # Access raw metadata
    # 0️⃣ --ids
    if args.ids:
        id_list = [cid.strip() for cid in args.ids.split(",") if cid.strip()]
        missing = [cid for cid in id_list if cid not in all_chals]
        if missing:
            known = list(all_chals.keys())
            raise ValueError(f"Challenge IDs not found: {missing}. Known IDs: {known[:10]}{'...' if len(known)>10 else ''}")
        return {cid: all_chals[cid] for cid in id_list}
    # 1️⃣ Single challenge by ID
    if args.challenge_id:
        if args.challenge_id not in all_chals:
            raise ValueError(f"Challenge '{args.challenge_id}' not found!")
        return {args.challenge_id: all_chals[args.challenge_id]}

    # 2️⃣ Filter by --benchmark
    if args.benchmark:
        benchmark_chals = {
            k: v for k, v in all_chals.items()
            if v.get("benchmark", "unknown").lower() == args.benchmark.lower()
        }
        if not benchmark_chals:
            known_benchmarks = sorted({v.get("benchmark", "unknown") for v in all_chals.values()})
            raise ValueError(f"No challenges found for benchmark '{args.benchmark}'. Available: {known_benchmarks}")

        if args.category:
            target_cats = {cat.strip().lower() for cat in args.category.split(",")}
            filtered = {
                k: v for k, v in benchmark_chals.items()
                if v.get("category", "unknown").lower() in target_cats
            }
            if not filtered:
                raise ValueError(f"No challenges match categories {target_cats} in benchmark '{args.benchmark}'.")
            return filtered
        return benchmark_chals

    # 3️⃣ Global --category
    if args.category:
        target_cats = {cat.strip().lower() for cat in args.category.split(",")}
        selected = {
            k: v for k, v in all_chals.items()
            if v.get("category", "unknown").lower() in target_cats
        }
        if not selected:
            raise ValueError(f"No challenges match categories: {target_cats}")
        return selected

    return all_chals


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Evolutionary CTF Agent Framework")
    parser.add_argument("--config", default="cyber_evolver/configs/evolve.yaml", help="Global config path")
    parser.add_argument(
        "--config-mode",
        choices=["evo", "evo_no_beam", "raw"],
        required=True,
        help=(
            "Select the built-in execution config profile. "
            "'evo' = full method (T=4, k=2, m=3). "
            "'evo_no_beam' = Ablation C — greedy sequential (T=16, k=1, m=1, no beam search). "
            "'raw' = no evolution (1 gen, 16 samples)."
        ),
    )
    parser.add_argument("--run-id", default="single_evo", help="Experiment name")
    parser.add_argument("--max-workers", type=int, default=1, help="Max parallel challenges")
    parser.add_argument("--base_seed_path", type=str, default="./cyber_evolver/seed_agent_templates/mini_cyberagent", help="Path to the base seed directory copied into the gen0 root.")
    parser.add_argument(
        "--seed-include",
        action="append",
        default=[],
        help="Relative path under --base_seed_path to copy into the materialized seed tree. Supported targets are commands/<file> and skills/<dir>.",
    )
    parser.add_argument("--task_workers", type=int, default=6, help="Max parallel tasks per challenge")
    parser.add_argument(
        "--category", "-c",
        default="",
        type=str,
        help="Filter by challenge category (comma-separated, e.g., 'pwn,rev,web,forensics')"
    )
    parser.add_argument(
        "--challenge-server-url",
        type=str,
        default=os.getenv("CHALLENGE_SERVER_URL", ""),
        help="URL of the challenge server. Defaults to CHALLENGE_SERVER_URL.",
    )

    parser.add_argument("--ids", type=str, help="Run specific challenges by ID list (comma-separated, e.g., 'pwn1,pwn2')")
    parser.add_argument("--challenge-id", "-i", type=str, help="Run single challenge by ID")
    parser.add_argument("--benchmark", type=str, default="nyu_ctf", help="Filter by benchmark tag")
    parser.add_argument("--evolve_prompt_cfg", type=str, default="cyber_evolver/configs/prompt.yml", help="Path to the evolution mutation prompt config YAML.")
    parser.add_argument(
        "--ablation",
        type=str,
        choices=["none", "holistic", "no_forensic"],
        default="none",
        help=(
            "Ablation mode. "
            "'holistic' (Ablation A): one-shot mutation, no layered phases — pair with --evolve_prompt_cfg cyber_evolver/configs/prompt_ablation_holistic.yml. "
            "'no_forensic' (Ablation B): keep 4-phase mutation but skip eureka diagnosis and use a simple summarizer — pair with --evolve_prompt_cfg cyber_evolver/configs/prompt_ablation_no_forensic.yml."
        ),
    )
    parser.add_argument("--model", "-m", type=str, help="LLM model name (from common/configs/model.yml)")
    parser.add_argument(
        "--prompt-variant",
        type=str,
        choices=["zero_day", "one_day"],
        default=None,
        help="Temporarily override the prompt variant for this run when a selected challenge exposes matching variant_names.",
    )

    # --- Token Budget / Live Usage ---
    parser.add_argument("--max-total-tokens", type=int, default=None, help="Global token budget across this run (all challenges).")
    parser.add_argument("--max-chal-tokens", type=int, default=None, help="Per-challenge token budget.")
    parser.add_argument("--enforce-token-budget", action="store_true", help="Hard-stop LLM calls when budget is exceeded.")
    parser.add_argument("--llm-max-inflight", type=int, default=6, help="Global concurrent LLM requests across all processes.")
    parser.add_argument("--llm-max-inflight-per-lane", type=int, default=2, help="Per-challenge concurrent LLM requests.")
    parser.add_argument("--llm-request-timeout", type=float, default=300.0, help="Timeout in seconds for each upstream LLM HTTP request.")
    parser.add_argument("--llm-max-attempts", type=int, default=5, help="Max retries performed by the centralized LLM dispatcher.")
    parser.add_argument("--llm-response-timeout", type=float, default=7200.0, help="Max time in seconds a caller waits for dispatcher completion.")
    parser.add_argument("--llm-fatal-window-seconds", type=float, default=30.0, help="Window size in seconds for dispatcher fatal-outage detection.")
    parser.add_argument("--llm-fatal-non200-threshold", type=int, default=20, help="Trip fatal outage when recent retryable non-200 failures reach this threshold.")
    parser.add_argument("--llm-fatal-total-fail-threshold", type=int, default=30, help="Trip fatal outage when recent breaker-relevant failures reach this threshold.")
    parser.add_argument("--llm-fatal-min-success", type=int, default=0, help="Maximum successes allowed alongside the recent total-failure threshold.")
    parser.add_argument("--llm-fatal-consecutive-fails", type=int, default=15, help="Trip fatal outage after this many consecutive breaker-relevant failures.")
    parser.add_argument("--llm-fatal-fail-rate-threshold", type=float, default=0.9, help="Trip fatal outage when the recent breaker failure rate reaches this threshold.")
    parser.add_argument("--llm-fatal-fail-rate-min-samples", type=int, default=40, help="Minimum recent samples before applying the fatal fail-rate threshold.")
    parser.add_argument("--llm-disable-fatal-breaker", action="store_true", help="Disable dispatcher fatal-outage breaker logic.")
    parser.add_argument("--llm-large-request-threshold", type=int, default=10000, help="Estimated input_tokens + max_tokens threshold above which dispatcher treats a request as large.")
    parser.add_argument("--llm-large-request-delay", type=float, default=4.0, help="Delay in seconds applied before sending a large dispatcher request.")

    return parser.parse_args(argv)
