from __future__ import annotations

import logging
import traceback
from concurrent.futures import as_completed
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Dict, List, Mapping, MutableMapping


def close_task_log_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    if logger is not None:
        try:
            if handler in logger.handlers:
                logger.removeHandler(handler)
        except Exception:
            pass

    try:
        handler.flush()
    except Exception:
        pass

    try:
        handler.close()
    except Exception:
        pass


def _append_budget_snapshot(
    *,
    chal_id: str,
    budget: Any,
    global_logger: logging.Logger,
) -> None:
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
    except Exception as e:
        global_logger.warning("[BUDGET] per-challenge snapshot failed (%s): %s", chal_id, e)


def _build_failed_result(
    *,
    chal_id: str,
    category: str,
    error: str,
    tb_text: str | None = None,
) -> Dict[str, Any]:
    result = {
        "chal_id": chal_id,
        "category": category,
        "status": "failed",
        "error": error,
    }
    if tb_text is not None:
        result["traceback"] = tb_text
    return result


def record_global_broken_pool_results(
    *,
    failed_context: Mapping[str, Any],
    inflight_contexts: Mapping[Any, Mapping[str, Any]],
    pending_items: List[Mapping[str, Any]],
    error: BrokenProcessPool,
    results: List[Dict[str, Any]],
    global_logger: logging.Logger,
) -> None:
    failed_chal_id = failed_context["chal_id"]
    failed_category = failed_context.get("category", "unknown")
    global_logger.warning(
        "⚠️ %s | Process pool broke during global result collection.",
        failed_chal_id,
    )
    results.append(
        _build_failed_result(
            chal_id=failed_chal_id,
            category=failed_category,
            error=str(error),
        )
    )

    for context in inflight_contexts.values():
        pending_chal_id = context["chal_id"]
        pending_category = context.get("category", "unknown")
        global_logger.warning(
            "⚠️ %s | Marking inflight challenge as failed after BrokenProcessPool.",
            pending_chal_id,
        )
        results.append(
            _build_failed_result(
                chal_id=pending_chal_id,
                category=pending_category,
                error=(
                    "Process pool became broken before result collection completed. "
                    f"Original error: {error}"
                ),
            )
        )

    for item in pending_items:
        pending_chal_id = item["chal_id"]
        pending_category = item.get("category", "unknown")
        global_logger.warning(
            "⚠️ %s | Marking pending challenge as failed before submission after BrokenProcessPool.",
            pending_chal_id,
        )
        results.append(
            _build_failed_result(
                chal_id=pending_chal_id,
                category=pending_category,
                error=(
                    "Process pool became broken before this challenge could be submitted. "
                    f"Original error: {error}"
                ),
            )
        )


def record_global_dispatcher_fatal_results(
    *,
    inflight_contexts: Mapping[Any, Mapping[str, Any]],
    pending_items: List[Mapping[str, Any]],
    error_message: str,
    results: List[Dict[str, Any]],
    global_logger: logging.Logger,
) -> None:
    for context in inflight_contexts.values():
        chal_id = context["chal_id"]
        category = context.get("category", "unknown")
        global_logger.warning(
            "⚠️ %s | Marking inflight challenge as failed after dispatcher fatal outage.",
            chal_id,
        )
        results.append(
            {
                **_build_failed_result(
                    chal_id=chal_id,
                    category=category,
                    error=(
                        "Dispatcher fatal outage detected before result collection completed. "
                        f"Original error: {error_message}"
                    ),
                ),
                "fatal_outage": True,
            }
        )

    for item in pending_items:
        chal_id = item["chal_id"]
        category = item.get("category", "unknown")
        global_logger.warning(
            "⚠️ %s | Marking pending challenge as failed before submission after dispatcher fatal outage.",
            chal_id,
        )
        results.append(
            {
                **_build_failed_result(
                    chal_id=chal_id,
                    category=category,
                    error=(
                        "Dispatcher fatal outage detected before this challenge could be submitted. "
                        f"Original error: {error_message}"
                    ),
                ),
                "fatal_outage": True,
            }
        )


def handle_global_dispatcher_fatal_outage(
    *,
    inflight_contexts: MutableMapping[Any, Mapping[str, Any]],
    pending_items: List[Mapping[str, Any]],
    error_message: str,
    results: List[Dict[str, Any]],
    global_logger: logging.Logger,
    executor: Any = None,
) -> Dict[str, int]:
    counts = {
        "completed_before_outage": sum(1 for item in results if not item.get("fatal_outage")),
        "inflight_failed_due_to_outage": len(inflight_contexts),
        "pending_failed_before_submission": len(pending_items),
    }

    record_global_dispatcher_fatal_results(
        inflight_contexts=inflight_contexts,
        pending_items=pending_items,
        error_message=error_message,
        results=results,
        global_logger=global_logger,
    )

    if executor is not None:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    inflight_contexts.clear()
    pending_items.clear()
    return counts


def collect_category_results(
    *,
    futures: Mapping[Any, str],
    category: str,
    results: List[Dict[str, Any]],
    budget: Any,
    challenge_client: Any,
    global_logger: logging.Logger,
    executor: Any = None,
    as_completed_fn: Callable[[Mapping[Any, str]], Any] | None = None,
) -> bool:
    as_completed_impl = as_completed_fn or (lambda future_map: as_completed(future_map))
    processed_futures = set()

    for future in as_completed_impl(futures):
        chal_id = futures[future]
        processed_futures.add(future)
        skip_immediate_teardown = False
        broke_pool = False

        try:
            res = future.result()
            results.append(res)
            status = res.get("status", "unknown")
            sr = res.get("best_success_rate", 0.0)
            global_logger.info("✅ %s | Status: %s | Best SR: %.1f%%", chal_id, status, sr * 100)
            _append_budget_snapshot(chal_id=chal_id, budget=budget, global_logger=global_logger)
        except BrokenProcessPool as e:
            skip_immediate_teardown = True
            broke_pool = True
            global_logger.exception("❌ %s | Exception during evolution: %s", chal_id, e)
            global_logger.warning(
                "⚠️ %s | Process pool is broken; deferring immediate teardown for this challenge.",
                chal_id,
            )
            results.append(
                _build_failed_result(
                    chal_id=chal_id,
                    category=category,
                    error=str(e),
                    tb_text=traceback.format_exc(),
                )
            )

            if executor is not None:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass

            remaining = [
                pending_chal_id
                for pending_future, pending_chal_id in futures.items()
                if pending_future not in processed_futures
            ]
            for pending_chal_id in remaining:
                global_logger.warning(
                    "⚠️ %s | Marking as failed after BrokenProcessPool; immediate teardown deferred.",
                    pending_chal_id,
                )
                results.append(
                    _build_failed_result(
                        chal_id=pending_chal_id,
                        category=category,
                        error=(
                            "Process pool became broken before result collection completed. "
                            f"Original error: {e}"
                        ),
                    )
                )
        except Exception as e:
            global_logger.exception("❌ %s | Exception during evolution: %s", chal_id, e)
            results.append(
                _build_failed_result(
                    chal_id=chal_id,
                    category=category,
                    error=str(e),
                    tb_text=traceback.format_exc(),
                )
            )
        finally:
            if not skip_immediate_teardown:
                try:
                    challenge_client.finish_challenge(chal_id)
                    global_logger.info("🧹 Teardown target done: %s", chal_id)
                except Exception as e:
                    global_logger.warning("⚠️ Teardown target failed: %s | %s", chal_id, e)

        if broke_pool:
            return True

    return False
