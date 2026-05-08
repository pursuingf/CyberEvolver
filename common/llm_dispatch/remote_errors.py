"""Remote LLM HTTP error parsing, classification, and message formatting."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

import httpx


def _summarize_response_body(text: str, *, max_chars: int = 300) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."


def _flatten_remote_error_message(payload: Any) -> str:
    candidates: List[str] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 3 or value is None:
            return
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                candidates.append(stripped)
            return
        if isinstance(value, Mapping):
            for key in ("message", "detail", "error", "error_message", "msg", "title", "code"):
                if key in value:
                    visit(value.get(key), depth + 1)
            return
        if isinstance(value, list):
            for item in value[:3]:
                visit(item, depth + 1)

    visit(payload)
    combined = " | ".join(dict.fromkeys(candidates))
    return _summarize_response_body(combined)


def _classify_remote_error(status_code: Optional[int], summary: str) -> str:
    text = " ".join((summary or "").lower().split())
    if int(status_code or 0) == 429 or "rate limit" in text or "too many requests" in text:
        return "rate_limited"

    context_limit_patterns = (
        "maximum context length",
        "maximum model length",
        "maximum model len",
        "context length",
        "context window",
        "max context",
        "max_model_len",
        "max model len",
        "model max len",
        "max len",
        "input is too long",
        "prompt is too long",
        "requested tokens",
        "too many tokens",
        "exceeds the context",
        "exceed the context",
        "longer than the maximum",
    )
    if any(pattern in text for pattern in context_limit_patterns):
        return "request_context_limit"

    parameter_patterns = (
        "max_tokens",
        "parameter",
        "validation",
        "invalid",
        "must be less than",
        "must be smaller than",
        "must be <=",
        "out of range",
        "schema",
        "bad request",
    )
    if any(pattern in text for pattern in parameter_patterns):
        return "request_parameter_invalid"

    if status_code is not None:
        code = int(status_code)
        if code in {400, 413, 422}:
            return "request_invalid"
        if 500 <= code < 600:
            return "service_unavailable"
    return "malformed_response"


def _build_remote_error_message(
    *,
    status_code: Optional[int],
    summary: str,
    body_preview: str,
    error_kind: str,
) -> str:
    label_map = {
        "request_context_limit": "remote request invalid (context_limit)",
        "request_parameter_invalid": "remote request invalid (parameter_invalid)",
        "request_invalid": "remote request invalid",
        "rate_limited": "remote rate limited request",
        "service_unavailable": "remote service unavailable",
        "malformed_response": "remote response malformed",
    }
    label = label_map.get(error_kind, "remote request failed")
    parts: List[str] = []
    if status_code is not None:
        parts.append(f"status={int(status_code)}")
    if summary:
        parts.append(summary)
    if body_preview and body_preview not in summary:
        parts.append(f"body_preview={body_preview}")
    if not parts:
        return label
    return f"{label}: {' | '.join(parts)}"


def _extract_remote_error_details(resp: httpx.Response) -> Dict[str, Any]:
    body_preview = _summarize_response_body(resp.text)
    payload: Any = None
    try:
        payload = resp.json()
    except Exception:
        payload = None

    summary = _flatten_remote_error_message(payload) if payload is not None else body_preview
    if not summary:
        summary = body_preview
    error_kind = _classify_remote_error(resp.status_code, summary)
    return {
        "summary": summary,
        "body_preview": body_preview,
        "error_kind": error_kind,
        "error_message": _build_remote_error_message(
            status_code=resp.status_code,
            summary=summary,
            body_preview=body_preview,
            error_kind=error_kind,
        ),
    }
