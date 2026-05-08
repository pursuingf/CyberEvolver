"""LLM dispatcher fatal-state helpers + agent-runtime network sync."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, MutableMapping, Optional

from common.agent_runtime.docker_env import DockerEnvironment
from common.llm_dispatch.dispatcher import LLMDispatcherHandle, format_dispatcher_fatal_message
from common.utils.process_pool_guards import handle_global_dispatcher_fatal_outage


def sync_agent_runtime_network(env: DockerEnvironment, chal_data: Dict[str, Any]) -> None:
    runtime = dict(chal_data.get("runtime", {}) or {})
    network_name = runtime.get("network_name")
    if not network_name:
        return
    if env.config.network_name == network_name:
        return

    env._connect_to_network(str(network_name))
    env.config.network_name = str(network_name)


def get_dispatcher_fatal_snapshot(dispatcher_handle: Optional[LLMDispatcherHandle]) -> Dict[str, Any]:
    if dispatcher_handle is None:
        return {}
    fatal_state = getattr(dispatcher_handle, "fatal_state", None)
    if fatal_state is None:
        return {}
    try:
        snapshot = dict(fatal_state)
    except Exception:
        return {}
    if not snapshot.get("active"):
        return {}
    return snapshot


def stop_for_active_dispatcher_fatal(
    *,
    dispatcher_handle: Optional[LLMDispatcherHandle],
    inflight_futures: MutableMapping[Any, Dict[str, Any]],
    pending_items: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    global_logger: logging.Logger,
    executor: Any = None,
) -> Optional[Dict[str, int]]:
    fatal_snapshot = get_dispatcher_fatal_snapshot(dispatcher_handle)
    if not fatal_snapshot:
        return None

    global_logger.error("🛑 LLM dispatcher fatal outage detected. Stopping the run early.")
    fatal_counts = handle_global_dispatcher_fatal_outage(
        inflight_contexts=inflight_futures,
        pending_items=pending_items,
        error_message=format_dispatcher_fatal_message(fatal_snapshot),
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
    return fatal_counts
