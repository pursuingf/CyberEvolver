"""Challenge-slot scheduling and progress-formatting helpers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, List, MutableMapping, Optional

from run_evolve.runtime_args import (
    apply_prompt_variant_override,
    load_challenge_data_for_submission,
)


def fill_available_challenge_slots(
    *,
    pending_items: List[Dict[str, Any]],
    inflight_futures: MutableMapping[Any, Dict[str, Any]],
    max_workers: int,
    load_challenge_data: Callable[[str], Any],
    submit_challenge: Callable[[Dict[str, Any], Any], Any],
    on_submit_error: Optional[Callable[[Dict[str, Any], Exception], None]] = None,
    resolve_runtime_args: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    prompt_variant: Optional[str] = None,
) -> None:
    """
    Lazily initialize and submit challenges until the process-pool capacity is full.

    `pending_items` is mutated in-place by removing items that have been submitted.
    `inflight_futures` is mutated in-place by registering the submitted future with
    the challenge context needed later for result collection and teardown.
    """
    while pending_items and len(inflight_futures) < max_workers:
        item = pending_items.pop(0)
        chal_id = item["chal_id"]
        runtime_args = dict(resolve_runtime_args(item) or {}) if resolve_runtime_args is not None else {}
        try:
            chal_data = load_challenge_data_for_submission(
                load_challenge_data=load_challenge_data,
                chal_id=chal_id,
                chal_meta=item.get("chal_meta", {}),
                runtime_args=runtime_args,
            )
            chal_data = apply_prompt_variant_override(chal_data, prompt_variant)
        except Exception as exc:
            if on_submit_error is not None:
                on_submit_error(item, exc)
            continue
        future = submit_challenge(item, chal_data)
        inflight_futures[future] = {
            **item,
            "chal_data": chal_data,
        }


def build_pending_challenge_items(target_chals_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    items_by_category = defaultdict(list)
    for chal_id, chal_meta in target_chals_meta.items():
        category = chal_meta.get("category", "unknown")
        items_by_category[category].append({
            "chal_id": chal_id,
            "category": category,
            "chal_meta": chal_meta,
        })
    pending_items = []
    for category in sorted(items_by_category):
        pending_items.extend(items_by_category[category])
    return pending_items


def format_category_mix(pending_items: List[Dict[str, Any]]) -> str:
    counts = defaultdict(int)
    for item in pending_items:
        counts[item.get("category", "unknown")] += 1
    return ", ".join(f"{cat}={counts[cat]}" for cat in sorted(counts))


def format_scheduler_category_progress(
    results: List[Dict[str, Any]],
    inflight_futures: MutableMapping[Any, Dict[str, Any]],
    pending_items: List[Dict[str, Any]],
) -> str:
    stats = defaultdict(lambda: {"done": 0, "total": 0, "inflight": 0, "pending": 0, "solved": 0})

    for result in results:
        category = result.get("category", "unknown")
        stats[category]["done"] += 1
        stats[category]["total"] += 1
        if result.get("best_success_rate", 0.0) >= 0.3:
            stats[category]["solved"] += 1

    for context in inflight_futures.values():
        category = context.get("category", "unknown")
        stats[category]["inflight"] += 1
        stats[category]["total"] += 1

    for item in pending_items:
        category = item.get("category", "unknown")
        stats[category]["pending"] += 1
        stats[category]["total"] += 1

    return " | ".join(
        (
            f"{category}: done={values['done']}/{values['total']} "
            f"inflight={values['inflight']} pending={values['pending']} solved={values['solved']}"
        )
        for category, values in sorted(stats.items())
    )
