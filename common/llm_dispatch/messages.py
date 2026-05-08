from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


def _normalize_timeout_seconds(timeout: Any, default: float = 300.0) -> float:
    if timeout is None:
        return default
    try:
        value = float(timeout)
    except Exception:
        return default
    if value <= 0:
        return default
    # Existing code often passes millisecond-like values such as 300000.
    if value > 10000:
        return value / 1000.0
    return value


def _message_role_from_obj(message: Any) -> str:
    msg_type = getattr(message, "type", None)
    if isinstance(msg_type, str):
        mapping = {
            "human": "user",
            "system": "system",
            "ai": "assistant",
            "assistant": "assistant",
            "user": "user",
        }
        return mapping.get(msg_type, msg_type)
    cls_name = type(message).__name__.lower()
    if "human" in cls_name:
        return "user"
    if "system" in cls_name:
        return "system"
    if "ai" in cls_name or "assistant" in cls_name:
        return "assistant"
    return "user"


def _serialize_tool_call(tc: Any) -> Dict[str, Any]:
    """Serialize a single tool_call (OpenAI format) to a plain dict."""
    if isinstance(tc, Mapping):
        return dict(tc)
    result: Dict[str, Any] = {}
    if hasattr(tc, "id"):
        result["id"] = getattr(tc, "id", "")
    if hasattr(tc, "type"):
        result["type"] = getattr(tc, "type", "function")
    if hasattr(tc, "function"):
        func = tc.function
        if isinstance(func, Mapping):
            result["function"] = dict(func)
        elif hasattr(func, "name") and hasattr(func, "arguments"):
            result["function"] = {"name": func.name, "arguments": func.arguments}
        else:
            result["function"] = {"name": str(func), "arguments": "{}"}
    return result


def serialize_messages(messages: Iterable[Any]) -> List[Dict[str, Any]]:
    """Serialize messages to OpenAI-compatible dicts, preserving tool_calls and tool_call_id."""
    serialized: List[Dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Mapping):
            msg: Dict[str, Any] = {
                "role": str(message.get("role", "user")),
                "content": message.get("content", ""),
            }
            if message.get("tool_call_id"):
                msg["tool_call_id"] = str(message["tool_call_id"])
            raw_tcs = message.get("tool_calls")
            if raw_tcs:
                msg["tool_calls"] = [_serialize_tool_call(tc) for tc in raw_tcs]
            if message.get("name"):
                msg["name"] = str(message["name"])
            serialized.append(msg)
            continue
        if hasattr(message, "content"):
            msg = {
                "role": _message_role_from_obj(message),
                "content": getattr(message, "content", ""),
            }
            raw_tcs = getattr(message, "tool_calls", None)
            if raw_tcs:
                msg["tool_calls"] = [_serialize_tool_call(tc) for tc in raw_tcs]
            tc_id = getattr(message, "tool_call_id", None)
            if tc_id:
                msg["tool_call_id"] = str(tc_id)
            name = getattr(message, "name", None)
            if name:
                msg["name"] = str(name)
            serialized.append(msg)
            continue
        raise TypeError(f"Unsupported message type: {type(message)!r}")
    return serialized


def is_retryable_status(status_code: int) -> bool:
    return int(status_code) == 429 or 500 <= int(status_code) < 600


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)
