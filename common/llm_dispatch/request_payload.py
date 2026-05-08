"""Request payload assembly + token usage estimation."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping

from .messages import _extract_text_content

if TYPE_CHECKING:
    from .dispatcher import LLMDispatchRequest


def _build_payload(request: "LLMDispatchRequest") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": request.model,
        "messages": request.messages,
        "stream": False,
    }
    request_params = dict(request.request_params or {})
    if "tools" in request_params:
        payload["tools"] = request_params.pop("tools")
    if "tool_choice" in request_params:
        payload["tool_choice"] = request_params.pop("tool_choice")
    payload.update(request_params)
    return payload


def _estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    char_estimate = (len(text) + 3) // 4
    non_whitespace_chars = sum(1 for ch in text if not ch.isspace())
    dense_text_estimate = (non_whitespace_chars + 1) // 2
    return max(1, char_estimate, dense_text_estimate)


def _estimate_request_token_usage(
    messages: Iterable[Mapping[str, Any]],
    request_params: Mapping[str, Any],
    large_request_threshold: int,
) -> Dict[str, Any]:
    estimated_input_tokens = 0
    for message in messages:
        estimated_input_tokens += _estimate_text_tokens(_extract_text_content(message.get("content", ""))) + 4
    max_tokens = int(request_params.get("max_tokens", request_params.get("max_completion_tokens", 0)) or 0)
    estimated_total_tokens = estimated_input_tokens + max_tokens
    return {
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_total_tokens": estimated_total_tokens,
        "is_large_request": estimated_total_tokens > int(large_request_threshold),
    }
