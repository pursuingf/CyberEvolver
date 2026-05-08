from __future__ import annotations

from typing import Any, Mapping


VALID_TARGET_SCOPES = {"per_challenge", "per_agent"}


def normalize_target_scope(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_TARGET_SCOPES:
        return normalized
    return ""


def resolve_target_scope(
    chal_data: Mapping[str, Any] | None = None,
    runtime_args: Mapping[str, Any] | None = None,
) -> str:
    requested = normalize_target_scope((runtime_args or {}).get("target_scope"))
    if requested:
        return requested

    benchmark_family = str((chal_data or {}).get("benchmark_family", "") or "").strip().lower()
    if benchmark_family == "cvebench":
        return "per_agent"
    return "per_challenge"


def should_auto_init_target(
    chal_data: Mapping[str, Any] | None = None,
    runtime_args: Mapping[str, Any] | None = None,
) -> bool:
    return resolve_target_scope(chal_data=chal_data, runtime_args=runtime_args) != "per_agent"
