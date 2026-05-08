from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import random
import re
import signal
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.agent_runtime.challenge_client import ChallengeClientConfig, ChallengeClient
from common.llm_dispatch.dispatcher import LLMDispatcherRuntime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATEGORY_A: Set[str] = {"cy_agent", "vulnbot", "autopenbench", "reasoningbank_agent", "ace_agent", "ace_bash_agent"}
CATEGORY_B: Set[str] = {"nyuctf_single", "dcipher", "t_agent"}
ALL_AGENTS = CATEGORY_A | CATEGORY_B
ACE_EVOLUTION_AGENTS: Set[str] = {"ace_agent", "ace_bash_agent"}

BATCH_LOG_ROOT = Path("baseline/logs/batch")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WorkItem:
    chal_id: str
    sample_idx: int

    def key(self) -> str:
        return f"{self.chal_id}::s{self.sample_idx}"


@dataclass
class ChallengeResult:
    chal_id: str
    sample_idx: int
    category: str
    benchmark: str
    solved: bool = False
    error: Optional[str] = None
    duration_s: float = 0.0
    flag: Optional[str] = None
    iterations_completed: Optional[int] = None
    solved_at_iteration: Optional[int] = None
    tokens_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    solve_tokens_total: int = 0
    solve_tokens_input: int = 0
    solve_tokens_output: int = 0
    reflector_tokens_total: int = 0
    reflector_tokens_input: int = 0
    reflector_tokens_output: int = 0


@dataclass
class AceIterationResult:
    chal_id: str
    category: str
    benchmark: str
    iteration: int
    solved: bool = False
    error: Optional[str] = None
    duration_s: float = 0.0
    flag: Optional[str] = None
    playbook_version_in: int = 0
    playbook_version_out: int = 0
    tokens_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    solve_tokens_total: int = 0
    solve_tokens_input: int = 0
    solve_tokens_output: int = 0
    reflector_tokens_total: int = 0
    reflector_tokens_input: int = 0
    reflector_tokens_output: int = 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch baseline execution for CTF agents"
    )
    parser.add_argument(
        "--agent",
        required=True,
        choices=sorted(ALL_AGENTS),
        help="Agent name (must match a config in baseline/configs/)",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model name key in common/configs/model.yml",
    )
    parser.add_argument(
        "--benchmark",
        default=None,
        help="Filter challenges by benchmark name",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=None,
        dest="categories",
        help="Filter by challenge category (repeatable)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Max concurrent workers (default: 2)",
    )
    parser.add_argument(
        "--step-limit",
        type=int,
        default=10,
        help="Max agent steps per challenge (default: 10)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run identifier (auto-generated if omitted)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing checkpoint, skipping completed items",
    )
    parser.add_argument(
        "--resume-run-dir",
        default=None,
        help="Existing run directory to extend when using ACE challenge evolution.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Number of samples per challenge (default: 1)",
    )
    parser.add_argument(
        "--challenges",
        default=None,
        help="Comma-separated list of challenge IDs to run (overrides benchmark/category filters)",
    )
    parser.add_argument(
        "--challenge-server-url",
        default=None,
        help="Override challenge_client.server_url from the agent config",
    )
    parser.add_argument(
        "--prompt-variant",
        choices=["zero_day", "one_day"],
        default=None,
        help="Override CVEBench prompt variant when supported by the selected challenge.",
    )
    parser.add_argument(
        "--ace-prompt-profile",
        default=None,
        help="Override the ACE system prompt profile from baseline/configs/prompt.yml.",
    )
    parser.add_argument(
        "--ace-playbook-scope",
        choices=["global", "benchmark", "category", "challenge"],
        default="benchmark",
        help="Scope for sharing ACE playbooks across challenges.",
    )
    parser.add_argument(
        "--ace-batch-size",
        type=int,
        default=None,
        help="Per-scope lane width before one serial ACE curate step.",
    )
    parser.add_argument(
        "--ace-batch-order",
        choices=["sorted", "random"],
        default="sorted",
        help="Ordering strategy for ACE batches within each playbook scope.",
    )
    parser.add_argument(
        "--ace-curate-mode",
        choices=["per-item", "batch"],
        default="batch",
        help="ACE update mode. Use batch for parallel solve + serial curate.",
    )
    parser.add_argument(
        "--ace-worker-allocation",
        choices=["lane-balanced", "global"],
        default="lane-balanced",
        help="How global workers are distributed across ACE scopes.",
    )
    parser.add_argument(
        "--ace-evolve-mode",
        choices=["batch", "challenge"],
        default="batch",
        help="ACE evolution order. batch keeps the previous parallel solve + serial curate flow; challenge evolves each challenge independently.",
    )
    parser.add_argument(
        "--ace-evolve-depth",
        type=int,
        default=1,
        help="Number of serial ACE attempts per challenge when --ace-evolve-mode=challenge.",
    )
    parser.add_argument(
        "--ace-extend-depth",
        type=int,
        default=None,
        help="Append this many more ACE challenge-evolution iterations beyond the existing depth.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _timestamp_str() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _is_ace_evolution_agent(agent_name: str) -> bool:
    return str(agent_name or "") in ACE_EVOLUTION_AGENTS


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _env_flag_enabled(name: str) -> bool:
    value = str(os.environ.get(name, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def prepare_model_kwargs_for_dispatch(model_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    prepared = dict(model_kwargs or {})
    prepared["thinking"] = False
    chat_template_kwargs = dict(prepared.get("chat_template_kwargs") or {})
    chat_template_kwargs["enable_thinking"] = False
    prepared["chat_template_kwargs"] = chat_template_kwargs
    return prepared


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _build_run_dir(agent_name: str, model_name: str, run_id: Optional[str]) -> Path:
    ts = _timestamp_str()
    rid = run_id or "run"
    return BATCH_LOG_ROOT / agent_name / model_name / f"{ts}_{rid}"


def _enumerate_challenges(
    challenge_client: ChallengeClient,
    benchmark_filter: Optional[str],
    category_filters: Optional[List[str]],
    challenge_ids: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return challenges dict filtered by benchmark and/or category."""
    result: Dict[str, Dict[str, Any]] = {}
    for chal_id, meta in challenge_client.challenges.items():
        if challenge_ids is not None and chal_id not in challenge_ids:
            continue
        if benchmark_filter and meta.get("benchmark") != benchmark_filter:
            continue
        if category_filters and meta.get("category") not in category_filters:
            continue
        result[chal_id] = meta
    return result


def _build_work_items(
    challenges: Dict[str, Dict[str, Any]],
    num_samples: int,
) -> List[WorkItem]:
    items: List[WorkItem] = []
    for chal_id in sorted(challenges.keys()):
        for sidx in range(num_samples):
            items.append(WorkItem(chal_id=chal_id, sample_idx=sidx))
    return items


def _ace_scope_key(scope: str, chal_id: str, meta: Dict[str, Any]) -> str:
    if scope == "global":
        return "global"
    if scope == "benchmark":
        return str(meta.get("benchmark", "unknown"))
    if scope == "category":
        return str(meta.get("category", "unknown"))
    if scope == "challenge":
        return chal_id
    raise ValueError(f"Unsupported ACE scope: {scope}")


def _build_ace_lanes(
    work_items: List[WorkItem],
    challenges: Dict[str, Dict[str, Any]],
    scope: str,
    batch_order: str,
) -> Dict[str, List[WorkItem]]:
    lanes: Dict[str, List[WorkItem]] = defaultdict(list)
    for item in work_items:
        meta = challenges.get(item.chal_id, {})
        lanes[_ace_scope_key(scope, item.chal_id, meta)].append(item)

    for scope_key, lane_items in lanes.items():
        lane_items.sort(key=lambda item: (item.chal_id, item.sample_idx))
        if batch_order == "random":
            rng = random.Random(scope_key)
            rng.shuffle(lane_items)
    return dict(lanes)


def _pop_next_lane_batch(lane_items: List[WorkItem], ace_batch_size: int) -> List[WorkItem]:
    batch_size = max(1, ace_batch_size)
    batch = lane_items[:batch_size]
    del lane_items[:batch_size]
    return batch


def _load_completed_keys(checkpoint_path: Path) -> Set[str]:
    """Load set of completed work-item keys from checkpoint.jsonl."""
    keys: Set[str] = set()
    if not checkpoint_path.exists():
        return keys
    with open(checkpoint_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                key = record.get("key")
                if key:
                    keys.add(key)
            except json.JSONDecodeError:
                continue
    return keys


def _append_checkpoint(checkpoint_path: Path, result: ChallengeResult) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "key": f"{result.chal_id}::s{result.sample_idx}",
        "chal_id": result.chal_id,
        "sample_idx": result.sample_idx,
        "solved": result.solved,
        "flag": result.flag,
        "error": result.error,
        "duration_s": result.duration_s,
        "tokens_total": result.tokens_total,
        "tokens_input": result.tokens_input,
        "tokens_output": result.tokens_output,
        "solve_tokens_total": result.solve_tokens_total,
        "solve_tokens_input": result.solve_tokens_input,
        "solve_tokens_output": result.solve_tokens_output,
        "reflector_tokens_total": result.reflector_tokens_total,
        "reflector_tokens_input": result.reflector_tokens_input,
        "reflector_tokens_output": result.reflector_tokens_output,
        "ts": _iso_now(),
    }
    with open(checkpoint_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_batch_meta(
    meta_path: Path,
    agent_name: str,
    model_name: str,
    run_id: str,
    timestamp: str,
    args_dict: Dict[str, Any],
    total_challenges: int,
    started_at: str,
    completed_at: Optional[str] = None,
) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent": agent_name,
        "model": model_name,
        "run_id": run_id,
        "timestamp": timestamp,
        "args": args_dict,
        "total_challenges": total_challenges,
        "started_at": started_at,
        "completed_at": completed_at,
    }
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _write_batch_results_json(json_path: Path, results: List[ChallengeResult]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for r in results:
        records.append({
            "chal_id": r.chal_id,
            "sample_idx": r.sample_idx,
            "category": r.category,
            "benchmark": r.benchmark,
            "solved": r.solved,
            "flag": r.flag,
            "error": r.error,
            "duration_s": round(r.duration_s, 2),
            "iterations_completed": r.iterations_completed,
            "solved_at_iteration": r.solved_at_iteration,
            "tokens_total": r.tokens_total,
            "tokens_input": r.tokens_input,
            "tokens_output": r.tokens_output,
            "solve_tokens_total": r.solve_tokens_total,
            "solve_tokens_input": r.solve_tokens_input,
            "solve_tokens_output": r.solve_tokens_output,
            "reflector_tokens_total": r.reflector_tokens_total,
            "reflector_tokens_input": r.reflector_tokens_input,
            "reflector_tokens_output": r.reflector_tokens_output,
        })
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)


def _write_iteration_results_json(json_path: Path, results: List[AceIterationResult]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for r in results:
        records.append({
            "chal_id": r.chal_id,
            "category": r.category,
            "benchmark": r.benchmark,
            "iteration": r.iteration,
            "solved": r.solved,
            "flag": r.flag,
            "error": r.error,
            "duration_s": round(r.duration_s, 2),
            "playbook_version_in": r.playbook_version_in,
            "playbook_version_out": r.playbook_version_out,
            "tokens_total": r.tokens_total,
            "tokens_input": r.tokens_input,
            "tokens_output": r.tokens_output,
            "solve_tokens_total": r.solve_tokens_total,
            "solve_tokens_input": r.solve_tokens_input,
            "solve_tokens_output": r.solve_tokens_output,
            "reflector_tokens_total": r.reflector_tokens_total,
            "reflector_tokens_input": r.reflector_tokens_input,
            "reflector_tokens_output": r.reflector_tokens_output,
        })
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)


def _load_iteration_results(json_path: Path) -> List[AceIterationResult]:
    if not json_path.exists():
        return []
    try:
        raw_records = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw_records, list):
        return []

    results: List[AceIterationResult] = []
    for row in raw_records:
        if not isinstance(row, dict):
            continue
        results.append(
            AceIterationResult(
                chal_id=str(row.get("chal_id", "")),
                category=str(row.get("category", "unknown") or "unknown"),
                benchmark=str(row.get("benchmark", "unknown") or "unknown"),
                iteration=int(row.get("iteration", 0) or 0),
                solved=bool(row.get("solved", False)),
                error=row.get("error"),
                duration_s=float(row.get("duration_s", 0.0) or 0.0),
                flag=row.get("flag"),
                playbook_version_in=int(row.get("playbook_version_in", 0) or 0),
                playbook_version_out=int(row.get("playbook_version_out", 0) or 0),
                tokens_total=int(row.get("tokens_total", 0) or 0),
                tokens_input=int(row.get("tokens_input", 0) or 0),
                tokens_output=int(row.get("tokens_output", 0) or 0),
                solve_tokens_total=int(row.get("solve_tokens_total", 0) or 0),
                solve_tokens_input=int(row.get("solve_tokens_input", 0) or 0),
                solve_tokens_output=int(row.get("solve_tokens_output", 0) or 0),
                reflector_tokens_total=int(row.get("reflector_tokens_total", 0) or 0),
                reflector_tokens_input=int(row.get("reflector_tokens_input", 0) or 0),
                reflector_tokens_output=int(row.get("reflector_tokens_output", 0) or 0),
            )
        )
    return results


def _load_ace_scope_state(scope_dir: Path) -> Dict[str, Any]:
    state_path = scope_dir / "state.json"
    if not state_path.exists():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(state, dict):
        return state
    return {}


def _challenge_was_solved(iteration_results: List[AceIterationResult], chal_id: str) -> Optional[AceIterationResult]:
    solved_rows = [
        row
        for row in iteration_results
        if row.chal_id == chal_id and row.solved
    ]
    if not solved_rows:
        return None
    return min(solved_rows, key=lambda row: row.iteration)


def _write_batch_results_md(md_path: Path, results: List[ChallengeResult]) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)

    # Group by category
    cat_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"solved": 0, "total": 0})
    for r in results:
        cat = r.category or "unknown"
        cat_stats[cat]["total"] += 1
        if r.solved:
            cat_stats[cat]["solved"] += 1

    total_solved = sum(v["solved"] for v in cat_stats.values())
    total_challenges = sum(v["total"] for v in cat_stats.values())

    lines: List[str] = []
    lines.append("| Category | Solved | Total | Rate |")
    lines.append("|----------|--------|-------|------|")
    for cat in sorted(cat_stats.keys()):
        s = cat_stats[cat]
        rate = f"{s['solved'] / s['total'] * 100:.0f}%" if s["total"] > 0 else "0%"
        lines.append(f"| {cat} | {s['solved']} | {s['total']} | {rate} |")
    overall_rate = f"{total_solved / total_challenges * 100:.0f}%" if total_challenges > 0 else "0%"
    lines.append(f"| **Total** | **{total_solved}** | **{total_challenges}** | **{overall_rate}** |")

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Worker dispatch
# ---------------------------------------------------------------------------

# Late import to avoid circulars — the worker module may not exist yet at
# module-load time but will be available at call time.
_worker_fn = None


def _get_worker_fn():
    global _worker_fn
    if _worker_fn is None:
        from baseline.batch.worker import run_single_challenge
        _worker_fn = run_single_challenge
    return _worker_fn


def _run_worker(
    item: WorkItem,
    agent_name: str,
    agent_config: Dict[str, Any],
    model_kwargs: Dict[str, Any],
    dispatcher_handle: Any,
    client_config: ChallengeClientConfig,
    step_limit: int,
    run_dir: Path,
    prompt_variant: Optional[str] = None,
) -> ChallengeResult:
    """Invoke the worker for a single work item."""
    fn = _get_worker_fn()
    chal_dir = run_dir / "challenges" / "<category>" / item.chal_id
    return fn(
        chal_id=item.chal_id,
        sample_idx=item.sample_idx,
        agent_name=agent_name,
        agent_config=agent_config,
        model_kwargs=model_kwargs,
        dispatcher_handle=dispatcher_handle,
        client_config=client_config,
        step_limit=step_limit,
        log_dir=chal_dir,
        prompt_variant=prompt_variant,
    )


def _challenge_result_from_worker_output(
    raw: Any,
    item: WorkItem,
    meta: Dict[str, Any],
    error: Optional[str] = None,
) -> ChallengeResult:
    if error is not None:
        return ChallengeResult(
            chal_id=item.chal_id,
            sample_idx=item.sample_idx,
            category=meta.get("category", "unknown"),
            benchmark=meta.get("benchmark", "unknown"),
            solved=False,
            error=error,
        )
    if isinstance(raw, dict):
        return ChallengeResult(
            chal_id=raw.get("challenge_id", item.chal_id),
            sample_idx=raw.get("sample_idx", item.sample_idx),
            category=raw.get("category", meta.get("category", "unknown")),
            benchmark=raw.get("benchmark", meta.get("benchmark", "unknown")),
            solved=raw.get("solved", False),
            error=raw.get("error"),
            duration_s=raw.get("elapsed_seconds", 0.0),
            flag=raw.get("flag"),
            tokens_total=int(raw.get("tokens_total", 0) or 0),
            tokens_input=int(raw.get("tokens_input", 0) or 0),
            tokens_output=int(raw.get("tokens_output", 0) or 0),
            solve_tokens_total=int(raw.get("solve_tokens_total", 0) or 0),
            solve_tokens_input=int(raw.get("solve_tokens_input", 0) or 0),
            solve_tokens_output=int(raw.get("solve_tokens_output", 0) or 0),
            reflector_tokens_total=int(raw.get("reflector_tokens_total", 0) or 0),
            reflector_tokens_input=int(raw.get("reflector_tokens_input", 0) or 0),
            reflector_tokens_output=int(raw.get("reflector_tokens_output", 0) or 0),
        )
    return raw


def _challenge_log_dir(run_dir: Path, item: WorkItem, meta: Dict[str, Any]) -> Path:
    category = meta.get("category", "unknown")
    return run_dir / "challenges" / category / item.chal_id


def _safe_scope_dir_name(scope_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", scope_key)


def _log_worker_result(logger: logging.Logger, result: ChallengeResult) -> None:
    status = "SOLVED" if result.solved else "FAILED"
    logger.info(
        "[%s] %s (sample %d) — %s (%.1fs)",
        status,
        result.chal_id,
        result.sample_idx,
        "solved" if result.solved else (result.error or "no flag"),
        result.duration_s,
    )


def _run_ace_batch_workers(
    *,
    args: argparse.Namespace,
    logger: logging.Logger,
    remaining: List[WorkItem],
    challenges: Dict[str, Dict[str, Any]],
    run_dir: Path,
    agent_config: Dict[str, Any],
    model_kwargs: Dict[str, Any],
    runtime: LLMDispatcherRuntime,
    client_config: ChallengeClientConfig,
    checkpoint_path: Path,
    max_workers: int,
) -> List[ChallengeResult]:
    from baseline.batch.ace_curator import (
        INITIAL_PLAYBOOK,
        collect_batch_artifacts,
        curate_batch_playbook,
        load_playbook,
        save_playbook,
        write_scope_state,
    )

    ace_batch_size = int(args.ace_batch_size or max_workers)
    lanes = _build_ace_lanes(
        remaining,
        challenges,
        args.ace_playbook_scope,
        args.ace_batch_order,
    )
    scope_state: Dict[str, Dict[str, Any]] = {}
    for scope_key in lanes:
        scope_dir = run_dir / "ace_state" / _safe_scope_dir_name(scope_key)
        playbook_path = scope_dir / "playbook.txt"
        scope_state[scope_key] = {
            "scope_dir": scope_dir,
            "playbook_path": playbook_path,
            "playbook": load_playbook(playbook_path, INITIAL_PLAYBOOK),
            "version": 0,
            "batch_index": 0,
        }

    results: List[ChallengeResult] = []
    fn = _get_worker_fn()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending: Dict[Any, Tuple[WorkItem, str, Path]] = {}
        active_batches: Dict[str, Dict[str, Any]] = {}

        def submit_available_batches() -> None:
            if _shutdown_event.is_set():
                return
            slots = max_workers - len(pending)
            if slots <= 0:
                return
            for scope_key in sorted(lanes.keys()):
                if slots <= 0:
                    break
                if scope_key in active_batches or not lanes[scope_key]:
                    continue
                lane_width = ace_batch_size if args.ace_worker_allocation == "lane-balanced" else slots
                batch = _pop_next_lane_batch(lanes[scope_key], min(lane_width, slots))
                if not batch:
                    continue
                state = scope_state[scope_key]
                batch_index = int(state["batch_index"]) + 1
                batch_futures: Set[Any] = set()
                result_dirs: List[Path] = []
                for item in batch:
                    meta = challenges.get(item.chal_id, {})
                    chal_log_dir = _challenge_log_dir(run_dir, item, meta)
                    chal_log_dir.mkdir(parents=True, exist_ok=True)
                    future = executor.submit(
                        fn,
                        chal_id=item.chal_id,
                        sample_idx=item.sample_idx,
                        agent_name=args.agent,
                        agent_config=agent_config,
                        model_kwargs=model_kwargs,
                        dispatcher_handle=runtime.handle,
                        client_config=client_config,
                        step_limit=args.step_limit,
                        log_dir=chal_log_dir,
                        prompt_variant=args.prompt_variant,
                        ace_playbook_snapshot=state["playbook"],
                        ace_scope_key=scope_key,
                        ace_playbook_version=state["version"],
                        ace_disable_persist=True,
                    )
                    pending[future] = (item, scope_key, chal_log_dir)
                    batch_futures.add(future)
                    result_dirs.append(chal_log_dir)
                active_batches[scope_key] = {
                    "batch_index": batch_index,
                    "futures": batch_futures,
                    "result_dirs": result_dirs,
                }
                slots -= len(batch)
                logger.info(
                    "ACE lane %s submitted batch %d with %d items (playbook v%d)",
                    scope_key,
                    batch_index,
                    len(batch),
                    state["version"],
                )

        submit_available_batches()
        while pending and not _shutdown_event.is_set():
            done_batch: Set[Any] = set()
            try:
                for future in as_completed(set(pending.keys()), timeout=2.0):
                    done_batch.add(future)
                    item, scope_key, _result_dir = pending[future]
                    meta = challenges.get(item.chal_id, {})
                    try:
                        raw = future.result()
                        result = _challenge_result_from_worker_output(raw, item, meta)
                    except Exception as exc:
                        logger.exception("Worker failed for %s sample %d", item.chal_id, item.sample_idx)
                        result = _challenge_result_from_worker_output(None, item, meta, error=str(exc))
                    results.append(result)
                    _append_checkpoint(checkpoint_path, result)
                    _log_worker_result(logger, result)

                    active = active_batches.get(scope_key)
                    if active:
                        active["futures"].discard(future)
                        if not active["futures"]:
                            state = scope_state[scope_key]
                            scope_dir = state["scope_dir"]
                            batch_index = int(active["batch_index"])
                            artifacts = collect_batch_artifacts(active["result_dirs"])
                            curator_input = {
                                "scope_key": scope_key,
                                "playbook_version": state["version"],
                                "batch_index": batch_index,
                                "artifact_count": len(artifacts),
                                "artifacts": artifacts,
                            }
                            scope_dir.mkdir(parents=True, exist_ok=True)
                            (scope_dir / f"batch_{batch_index:04d}_curator_input.json").write_text(
                                json.dumps(curator_input, indent=2, ensure_ascii=False, default=str),
                                encoding="utf-8",
                            )
                            updated, summary = curate_batch_playbook(
                                playbook=state["playbook"],
                                batch_artifacts=artifacts,
                                llm_stub=None,
                                logger=logger,
                            )
                            state["playbook"] = updated
                            state["version"] = int(state["version"]) + 1
                            state["batch_index"] = batch_index
                            save_playbook(state["playbook_path"], updated)
                            write_scope_state(
                                scope_dir,
                                version=state["version"],
                                batch_index=batch_index,
                                summary=summary,
                            )
                            (scope_dir / f"batch_{batch_index:04d}_curator_output.json").write_text(
                                json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                                encoding="utf-8",
                            )
                            logger.info(
                                "ACE lane %s curated batch %d -> playbook v%d",
                                scope_key,
                                batch_index,
                                state["version"],
                            )
                            del active_batches[scope_key]
                    break
            except TimeoutError:
                pass
            for future in done_batch:
                pending.pop(future, None)
            submit_available_batches()

        if _shutdown_event.is_set():
            logger.warning("Shutdown in progress — cancelling remaining ACE futures ...")
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

    return results


def _run_ace_challenge_lane(
    *,
    args: argparse.Namespace,
    logger: logging.Logger,
    item: WorkItem,
    meta: Dict[str, Any],
    run_dir: Path,
    agent_config: Dict[str, Any],
    model_kwargs: Dict[str, Any],
    runtime: LLMDispatcherRuntime,
    client_config: ChallengeClientConfig,
    existing_iteration_results: Optional[List[AceIterationResult]] = None,
) -> Tuple[ChallengeResult, List[AceIterationResult]]:
    from baseline.batch.ace_curator import (
        INITIAL_PLAYBOOK,
        collect_batch_artifacts,
        curate_batch_playbook,
        load_playbook,
        save_playbook,
        write_scope_state,
    )

    fn = _get_worker_fn()
    category = str(meta.get("category", "unknown") or "unknown")
    benchmark = str(meta.get("benchmark", "unknown") or "unknown")
    scope_key = item.chal_id
    scope_dir = run_dir / "ace_state" / _safe_scope_dir_name(scope_key)
    playbook_path = scope_dir / "playbook.txt"
    state = _load_ace_scope_state(scope_dir)
    playbook = load_playbook(playbook_path, INITIAL_PLAYBOOK)
    playbook_version = int(state.get("playbook_version", 0) or 0)
    start_iteration = int(state.get("last_batch_index", 0) or 0) + 1
    prior_iterations = existing_iteration_results or []
    iteration_results: List[AceIterationResult] = []
    aggregate = ChallengeResult(
        chal_id=item.chal_id,
        sample_idx=0,
        category=category,
        benchmark=benchmark,
        solved=False,
        duration_s=0.0,
        iterations_completed=max(0, start_iteration - 1),
    )

    solved_row = _challenge_was_solved(prior_iterations, item.chal_id)
    if solved_row is not None:
        aggregate.solved = True
        aggregate.flag = solved_row.flag
        aggregate.error = solved_row.error
        aggregate.solved_at_iteration = solved_row.iteration
        aggregate.iterations_completed = max(
            (row.iteration for row in prior_iterations if row.chal_id == item.chal_id),
            default=aggregate.iterations_completed or 0,
        )
        return aggregate, iteration_results

    ace_extend_depth = getattr(args, "ace_extend_depth", None)
    if ace_extend_depth is not None:
        target_iteration = max(start_iteration - 1, 0) + max(0, int(ace_extend_depth or 0))
    else:
        target_iteration = max(1, int(args.ace_evolve_depth or 1))

    if target_iteration < start_iteration:
        return aggregate, iteration_results

    for iteration in range(start_iteration, target_iteration + 1):
        if _shutdown_event.is_set():
            break

        iter_dir = run_dir / "challenges" / category / item.chal_id / f"iter_{iteration:04d}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        version_in = playbook_version
        logger.info(
            "ACE challenge %s iteration %d/%d submitted (playbook v%d)",
            item.chal_id,
            iteration,
            target_iteration,
            version_in,
        )
        stop_after_iteration = False
        try:
            raw = fn(
                chal_id=item.chal_id,
                sample_idx=0,
                agent_name=args.agent,
                agent_config=agent_config,
                model_kwargs=model_kwargs,
                dispatcher_handle=runtime.handle,
                client_config=client_config,
                step_limit=args.step_limit,
                log_dir=iter_dir,
                prompt_variant=args.prompt_variant,
                ace_playbook_snapshot=playbook,
                ace_scope_key=scope_key,
                ace_playbook_version=version_in,
                ace_disable_persist=True,
            )
            result = _challenge_result_from_worker_output(raw, item, meta)
        except Exception as exc:
            logger.exception(
                "ACE challenge %s iteration %d/%d failed",
                item.chal_id,
                iteration,
                target_iteration,
            )
            result = _challenge_result_from_worker_output(None, item, meta, error=str(exc))
            stop_after_iteration = True

        artifacts = collect_batch_artifacts([iter_dir])
        curator_input = {
            "scope_key": scope_key,
            "playbook_version": playbook_version,
            "iteration": iteration,
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        }
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / f"iteration_{iteration:04d}_curator_input.json").write_text(
            json.dumps(curator_input, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        updated, summary = curate_batch_playbook(
            playbook=playbook,
            batch_artifacts=artifacts,
            llm_stub=None,
            logger=logger,
        )
        playbook = updated
        playbook_version += 1
        save_playbook(playbook_path, updated)
        write_scope_state(
            scope_dir,
            version=playbook_version,
            batch_index=iteration,
            summary=summary,
        )
        (scope_dir / f"iteration_{iteration:04d}_curator_output.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        iteration_results.append(
            AceIterationResult(
                chal_id=result.chal_id,
                category=result.category,
                benchmark=result.benchmark,
                iteration=iteration,
                solved=result.solved,
                error=result.error,
                duration_s=result.duration_s,
                flag=result.flag,
                playbook_version_in=version_in,
                playbook_version_out=playbook_version,
                tokens_total=result.tokens_total,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
                solve_tokens_total=result.solve_tokens_total,
                solve_tokens_input=result.solve_tokens_input,
                solve_tokens_output=result.solve_tokens_output,
                reflector_tokens_total=result.reflector_tokens_total,
                reflector_tokens_input=result.reflector_tokens_input,
                reflector_tokens_output=result.reflector_tokens_output,
            )
        )
        aggregate.chal_id = result.chal_id
        aggregate.category = result.category
        aggregate.benchmark = result.benchmark
        aggregate.duration_s += result.duration_s
        aggregate.error = result.error
        aggregate.flag = result.flag
        aggregate.iterations_completed = iteration
        aggregate.tokens_total += result.tokens_total
        aggregate.tokens_input += result.tokens_input
        aggregate.tokens_output += result.tokens_output
        aggregate.solve_tokens_total += result.solve_tokens_total
        aggregate.solve_tokens_input += result.solve_tokens_input
        aggregate.solve_tokens_output += result.solve_tokens_output
        aggregate.reflector_tokens_total += result.reflector_tokens_total
        aggregate.reflector_tokens_input += result.reflector_tokens_input
        aggregate.reflector_tokens_output += result.reflector_tokens_output

        logger.info(
            "ACE challenge %s iteration %d/%d curated -> playbook v%d",
            item.chal_id,
            iteration,
            target_iteration,
            playbook_version,
        )
        if result.solved:
            aggregate.solved = True
            aggregate.solved_at_iteration = iteration
            break
        if stop_after_iteration:
            break

    return aggregate, iteration_results


def _run_ace_challenge_evolution_workers(
    *,
    args: argparse.Namespace,
    logger: logging.Logger,
    remaining: List[WorkItem],
    challenges: Dict[str, Dict[str, Any]],
    run_dir: Path,
    agent_config: Dict[str, Any],
    model_kwargs: Dict[str, Any],
    runtime: LLMDispatcherRuntime,
    client_config: ChallengeClientConfig,
    checkpoint_path: Path,
    max_workers: int,
) -> List[ChallengeResult]:
    results: List[ChallengeResult] = []
    iteration_results_path = run_dir / "iteration_results.json"
    iteration_results: List[AceIterationResult] = _load_iteration_results(iteration_results_path)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item: Dict[Any, WorkItem] = {}
        for item in remaining:
            if _shutdown_event.is_set():
                logger.warning("Shutdown requested — not submitting more ACE challenge lanes.")
                break
            meta = challenges.get(item.chal_id, {})
            future = executor.submit(
                _run_ace_challenge_lane,
                args=args,
                logger=logger,
                item=item,
                meta=meta,
                run_dir=run_dir,
                agent_config=agent_config,
                model_kwargs=model_kwargs,
                runtime=runtime,
                client_config=client_config,
                existing_iteration_results=iteration_results,
            )
            future_to_item[future] = item

        pending = set(future_to_item.keys())
        while pending and not _shutdown_event.is_set():
            done_batch: Set[Any] = set()
            try:
                for future in as_completed(pending, timeout=2.0):
                    done_batch.add(future)
                    item = future_to_item[future]
                    meta = challenges.get(item.chal_id, {})
                    try:
                        result, lane_iterations = future.result()
                    except Exception as exc:
                        logger.exception("ACE challenge lane failed for %s", item.chal_id)
                        result = _challenge_result_from_worker_output(None, item, meta, error=str(exc))
                        result.iterations_completed = 0
                        lane_iterations = []
                    results.append(result)
                    iteration_results.extend(lane_iterations)
                    _append_checkpoint(checkpoint_path, result)
                    _write_iteration_results_json(iteration_results_path, iteration_results)
                    _log_worker_result(logger, result)
                    break
            except TimeoutError:
                pass
            pending -= done_batch

        if _shutdown_event.is_set():
            logger.warning("Shutdown in progress — cancelling remaining ACE challenge lanes ...")
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

    if not iteration_results_path.exists():
        _write_iteration_results_json(iteration_results_path, iteration_results)
    return results


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

_shutdown_event = threading.Event()


def _signal_handler(signum: int, frame: Any) -> None:
    if _shutdown_event.is_set():
        # Second Ctrl+C — force exit immediately
        logging.getLogger("batch").warning("Force exit (second signal).")
        os._exit(1)
    logging.getLogger("batch").warning(
        "Received signal %s — requesting graceful shutdown (press Ctrl+C again to force) ...", signum
    )
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    # ---- Setup logging ----
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("batch")

    # ---- Install signal handlers ----
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # ---- Load agent config ----
    agent_config_path = _PROJECT_ROOT / "baseline" / "configs" / f"{args.agent}.yaml"
    if not agent_config_path.exists():
        logger.error("Agent config not found: %s", agent_config_path)
        sys.exit(1)
    agent_config = _load_yaml(agent_config_path)
    logger.info("Loaded agent config from %s", agent_config_path)
    if args.challenge_server_url:
        agent_config.setdefault("challenge_client", {})["server_url"] = args.challenge_server_url
        logger.info("Overriding challenge_client.server_url to %s", args.challenge_server_url)
    if _is_ace_evolution_agent(args.agent) and args.ace_prompt_profile:
        agent_config.setdefault("agent", {}).setdefault("agent_kwargs", {})["prompt_profile"] = args.ace_prompt_profile
        logger.info("Overriding ACE prompt profile to %s", args.ace_prompt_profile)

    # ---- Load model config ----
    model_config_path = _PROJECT_ROOT / "common" / "configs" / "model.yml"
    if not model_config_path.exists():
        logger.error("Model config not found: %s", model_config_path)
        sys.exit(1)
    model_full_config = _load_yaml(model_config_path)
    if args.model not in model_full_config:
        logger.error(
            "Model '%s' not found in %s. Available: %s",
            args.model,
            model_config_path,
            list(model_full_config.keys()),
        )
        sys.exit(1)
    model_kwargs = model_full_config[args.model]
    # Merge agent-level model_kwargs (e.g. temperature) on top
    agent_model_overrides = agent_config.get("agent", {}).get("model_kwargs", {})
    if agent_model_overrides:
        merged = dict(agent_model_overrides)
        merged.update(model_kwargs)
        model_kwargs = merged
    if _env_flag_enabled("FORCE_DISABLE_THINKING"):
        model_kwargs = prepare_model_kwargs_for_dispatch(model_kwargs)
        logger.info("Forcing no-think model kwargs for this batch run")
    logger.info("Using model: %s", args.model)

    # ---- Prepare run directory ----
    timestamp = _timestamp_str()
    run_id = args.run_id or "run"
    if args.resume_run_dir:
        run_dir = Path(args.resume_run_dir)
    else:
        run_dir = _build_run_dir(args.agent, args.model, args.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Run directory: %s", run_dir)

    # ---- ChallengeClient ----
    client_cfg_dict = agent_config.get("challenge_client", {})
    client_config = ChallengeClientConfig(**client_cfg_dict)
    logger.info("Initializing ChallengeClient (run_mode=%s) ...", client_config.run_mode)
    challenge_client = ChallengeClient(config=client_config)

    # ---- Enumerate & filter challenges ----
    chal_id_list = args.challenges.split(",") if args.challenges else None
    challenges = _enumerate_challenges(
        challenge_client,
        benchmark_filter=args.benchmark,
        category_filters=args.categories,
        challenge_ids=chal_id_list,
    )
    if not challenges:
        logger.error("No challenges match the given filters.")
        sys.exit(1)
    logger.info("Challenges after filtering: %d", len(challenges))

    # ---- Build work items ----
    samples = 1 if _is_ace_evolution_agent(args.agent) and args.ace_evolve_mode == "challenge" else args.samples
    work_items = _build_work_items(challenges, samples)
    if _is_ace_evolution_agent(args.agent) and args.ace_evolve_mode == "challenge":
        logger.info(
            "Work items: %d (%d challenges x ACE evolve depth %d; samples disabled)",
            len(work_items),
            len(challenges),
            max(1, int(args.ace_evolve_depth or 1)),
        )
    else:
        logger.info("Work items: %d (%d challenges x %d samples)", len(work_items), len(challenges), args.samples)

    # ---- Resume support ----
    checkpoint_path = run_dir / "checkpoint.jsonl"
    completed_keys: Set[str] = set()
    use_checkpoint_resume = args.resume and not (
        _is_ace_evolution_agent(args.agent) and args.ace_evolve_mode == "challenge"
    )
    if use_checkpoint_resume:
        completed_keys = _load_completed_keys(checkpoint_path)
        logger.info("Resume mode: %d items already completed, will skip.", len(completed_keys))
    elif args.resume and _is_ace_evolution_agent(args.agent) and args.ace_evolve_mode == "challenge":
        logger.info("ACE challenge evolution resume uses ace_state; checkpoint skipping is disabled.")

    remaining = [item for item in work_items if item.key() not in completed_keys]
    if not remaining:
        logger.info("All work items already completed. Nothing to do.")
        # Still write summary from checkpoint data
        return

    logger.info("Remaining work items: %d", len(remaining))

    # ---- Write batch_meta.json ----
    started_at = _iso_now()
    args_dict = vars(args)
    meta_path = run_dir / "batch_meta.json"
    _write_batch_meta(
        meta_path=meta_path,
        agent_name=args.agent,
        model_name=args.model,
        run_id=run_id,
        timestamp=timestamp,
        args_dict=args_dict,
        total_challenges=len(work_items),
        started_at=started_at,
    )

    # ---- LLMDispatcherRuntime (shared) ----
    mp_context = multiprocessing.get_context("spawn")
    dispatcher_metrics_path = run_dir / "dispatcher_metrics.jsonl"
    dispatcher_summary_path = run_dir / "dispatcher_summary.log"
    runtime = LLMDispatcherRuntime(
        mp_context=mp_context,
        max_inflight=args.max_workers * 2,
        max_inflight_per_lane=args.max_workers,
        metrics_path=str(dispatcher_metrics_path),
        summary_log_path=str(dispatcher_summary_path),
    )
    runtime.start()
    logger.info("LLMDispatcherRuntime started.")

    # ---- Execute workers ----
    results: List[ChallengeResult] = []
    # Pre-populate results from checkpoint for previously completed items
    if completed_keys:
        for item in work_items:
            if item.key() in completed_keys:
                meta = challenges.get(item.chal_id, {})
                results.append(ChallengeResult(
                    chal_id=item.chal_id,
                    sample_idx=item.sample_idx,
                    category=meta.get("category", "unknown"),
                    benchmark=meta.get("benchmark", "unknown"),
                    solved=True,  # Previously completed — assume solved for summary
                ))

    max_workers = max(1, args.max_workers)
    logger.info("Dispatching %d items with %d workers ...", len(remaining), max_workers)

    try:
        if _is_ace_evolution_agent(args.agent) and args.ace_evolve_mode == "challenge":
            results.extend(
                _run_ace_challenge_evolution_workers(
                    args=args,
                    logger=logger,
                    remaining=remaining,
                    challenges=challenges,
                    run_dir=run_dir,
                    agent_config=agent_config,
                    model_kwargs=model_kwargs,
                    runtime=runtime,
                    client_config=client_config,
                    checkpoint_path=checkpoint_path,
                    max_workers=max_workers,
                )
            )
        elif _is_ace_evolution_agent(args.agent) and args.ace_curate_mode == "batch":
            results.extend(
                _run_ace_batch_workers(
                    args=args,
                    logger=logger,
                    remaining=remaining,
                    challenges=challenges,
                    run_dir=run_dir,
                    agent_config=agent_config,
                    model_kwargs=model_kwargs,
                    runtime=runtime,
                    client_config=client_config,
                    checkpoint_path=checkpoint_path,
                    max_workers=max_workers,
                )
            )
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_item: Dict[Any, WorkItem] = {}
                for item in remaining:
                    if _shutdown_event.is_set():
                        logger.warning("Shutdown requested — not submitting more items.")
                        break
                    meta = challenges.get(item.chal_id, {})
                    chal_log_dir = _challenge_log_dir(run_dir, item, meta)
                    chal_log_dir.mkdir(parents=True, exist_ok=True)

                    fn = _get_worker_fn()
                    future = executor.submit(
                        fn,
                        chal_id=item.chal_id,
                        sample_idx=item.sample_idx,
                        agent_name=args.agent,
                        agent_config=agent_config,
                        model_kwargs=model_kwargs,
                        dispatcher_handle=runtime.handle,
                        client_config=client_config,
                        step_limit=args.step_limit,
                        log_dir=chal_log_dir,
                        prompt_variant=args.prompt_variant,
                    )
                    future_to_item[future] = item

                pending = set(future_to_item.keys())
                while pending and not _shutdown_event.is_set():
                    done_batch: set = set()
                    try:
                        for future in as_completed(pending, timeout=2.0):
                            done_batch.add(future)
                            item = future_to_item[future]
                            meta = challenges.get(item.chal_id, {})
                            try:
                                raw = future.result()
                                result = _challenge_result_from_worker_output(raw, item, meta)
                            except Exception as exc:
                                logger.exception("Worker failed for %s sample %d", item.chal_id, item.sample_idx)
                                result = _challenge_result_from_worker_output(None, item, meta, error=str(exc))
                            results.append(result)
                            _append_checkpoint(checkpoint_path, result)
                            _log_worker_result(logger, result)
                    except TimeoutError:
                        pass
                    pending -= done_batch

                if _shutdown_event.is_set():
                    logger.warning("Shutdown in progress — cancelling remaining futures ...")
                    for f in pending:
                        f.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)

    finally:
        # ---- Shutdown dispatcher ----
        logger.info("Shutting down LLMDispatcherRuntime ...")
        runtime.shutdown(timeout_s=15.0)

    # ---- Write final outputs ----
    completed_at = _iso_now()
    _write_batch_meta(
        meta_path=meta_path,
        agent_name=args.agent,
        model_name=args.model,
        run_id=run_id,
        timestamp=timestamp,
        args_dict=args_dict,
        total_challenges=len(work_items),
        started_at=started_at,
        completed_at=completed_at,
    )

    results_json_path = run_dir / "batch_results.json"
    _write_batch_results_json(results_json_path, results)

    results_md_path = run_dir / "batch_results.md"
    _write_batch_results_md(results_md_path, results)

    # ---- Print summary ----
    solved = sum(1 for r in results if r.solved)
    total = len(results)
    rate = f"{solved / total * 100:.1f}%" if total > 0 else "0%"
    logger.info("=" * 60)
    logger.info("BATCH COMPLETE")
    logger.info("  Solved: %d / %d (%s)", solved, total, rate)
    logger.info("  Results: %s", results_json_path)
    logger.info("  Summary: %s", results_md_path)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
