from __future__ import annotations

from typing import Any, Dict, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .dispatcher import LLMDispatchResult


class LLMDispatcherError(RuntimeError):
    pass


class LLMDispatcherFatalError(LLMDispatcherError):
    pass


def _copy_fatal_state(fatal_state: Any) -> Dict[str, Any]:
    if fatal_state is None:
        return {}
    try:
        return dict(fatal_state)
    except Exception:
        return {}


def _fatal_state_is_active(fatal_state: Any) -> bool:
    return bool(_copy_fatal_state(fatal_state).get("active"))


def format_dispatcher_fatal_message(fatal_state: Mapping[str, Any]) -> str:
    if not fatal_state:
        return "dispatcher fatal outage detected"
    reason = fatal_state.get("reason", "unknown")
    summary = fatal_state.get("window_summary", {}) or {}
    last_error_type = fatal_state.get("last_error_type")
    last_error_kind = fatal_state.get("last_error_kind")
    last_status_code = fatal_state.get("last_status_code")
    return (
        "upstream LLM server outage detected: "
        f"reason={reason} "
        f"non200={summary.get('non200_failures', 0)} "
        f"total_failures={summary.get('total_failures', 0)} "
        f"successes={summary.get('successes', 0)} "
        f"consecutive_failures={summary.get('consecutive_failures', 0)} "
        f"last_error_type={last_error_type} "
        f"last_error_kind={last_error_kind} "
        f"last_status_code={last_status_code}"
    ).strip()


def _set_fatal_state(fatal_state: Any, payload: Mapping[str, Any]) -> None:
    if fatal_state is None:
        return
    try:
        fatal_state.clear()
    except Exception:
        pass
    try:
        fatal_state.update(dict(payload))
    except Exception:
        pass


def _is_breaker_relevant_result(result: "LLMDispatchResult") -> bool:
    if result.ok:
        return False
    if result.error_kind in {
        "request_context_limit",
        "request_parameter_invalid",
        "request_invalid",
        "rate_limited",
        "request_timeout",
        "malformed_response",
    }:
        return False
    if result.error_kind in {"connection_error", "service_unavailable"}:
        return True
    status_code = result.status_code
    if status_code is None:
        return True
    return 500 <= int(status_code) < 600


def _is_non200_breaker_result(result: "LLMDispatchResult") -> bool:
    if result.ok:
        return False
    if result.error_kind != "service_unavailable":
        return False
    status_code = result.status_code
    if status_code is None:
        return False
    return int(status_code) != 200 and 500 <= int(status_code) < 600


def _build_fatal_dispatch_result(request_id: str, fatal_state: Mapping[str, Any]) -> "LLMDispatchResult":
    from .dispatcher import LLMDispatchResult
    return LLMDispatchResult(
        ok=False,
        request_id=request_id,
        error_type="LLMDispatcherFatalError",
        error_kind="server_outage",
        error_message=format_dispatcher_fatal_message(fatal_state),
    )
