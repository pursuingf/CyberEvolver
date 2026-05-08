"""Per-challenge runtime-args + prompt-variant resolution helpers."""
from __future__ import annotations

import inspect
from copy import deepcopy
from typing import Any, Callable, Dict

from common.utils.runtime_policy import normalize_target_scope, should_auto_init_target


def resolve_benchmark_runtime_args(global_config: Dict[str, Any], chal_data: Dict[str, Any]) -> Dict[str, Any]:
    runtime_map = dict(global_config.get("benchmark_runtime_args") or {})
    normalized_runtime_map = {
        str(key).strip().lower(): dict(value or {})
        for key, value in runtime_map.items()
        if isinstance(value, dict)
    }

    resolved = dict(normalized_runtime_map.get("default", {}))
    benchmark_keys = [
        str(chal_data.get("benchmark_family", "") or "").strip().lower(),
        str(chal_data.get("benchmark", "") or "").strip().lower(),
        str(chal_data.get("benchmark_name", "") or "").strip().lower(),
    ]
    for benchmark_key in benchmark_keys:
        if benchmark_key and benchmark_key in normalized_runtime_map:
            resolved.update(normalized_runtime_map[benchmark_key])
            break
    return resolved


def filter_challenge_client_runtime_args(runtime_args: Dict[str, Any] | None) -> Dict[str, Any]:
    resolved = dict(runtime_args or {})
    filtered: Dict[str, Any] = {}

    parallel_mode = str(resolved.get("parallel_mode", "") or "").strip().lower()
    if parallel_mode:
        filtered["parallel_mode"] = parallel_mode

    target_scope = normalize_target_scope(resolved.get("target_scope"))
    if target_scope:
        filtered["target_scope"] = target_scope

    return filtered


def load_challenge_data_for_submission(
    load_challenge_data: Callable[..., Dict[str, Any]],
    chal_id: str,
    chal_meta: Dict[str, Any],
    runtime_args: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if runtime_args:
        kwargs["runtime_args"] = runtime_args

    if not should_auto_init_target(chal_data=chal_meta, runtime_args=runtime_args):
        try:
            signature = inspect.signature(load_challenge_data)
        except (TypeError, ValueError):
            signature = None
        if signature is not None and "auto_init" in signature.parameters:
            kwargs["auto_init"] = False

    if kwargs:
        return load_challenge_data(chal_id, **kwargs)
    return load_challenge_data(chal_id)


def resolve_challenge_client_runtime_args(global_config: Dict[str, Any], chal_data: Dict[str, Any]) -> Dict[str, Any]:
    benchmark_runtime_args = resolve_benchmark_runtime_args(global_config, chal_data)
    return filter_challenge_client_runtime_args(benchmark_runtime_args)


def apply_prompt_variant_override(chal_data: Dict[str, Any], prompt_variant: str | None) -> Dict[str, Any]:
    requested_variant = str(prompt_variant or "").strip().lower()
    if not requested_variant:
        return chal_data

    raw_variant_names = chal_data.get("variant_names")
    if not isinstance(raw_variant_names, list):
        return chal_data

    supported_variants = {
        str(name).strip().lower()
        for name in raw_variant_names
        if str(name).strip()
    }
    if requested_variant not in supported_variants:
        return chal_data

    updated = deepcopy(chal_data)
    updated["default_variant"] = requested_variant
    source_fields = updated.get("source_fields")
    if isinstance(source_fields, dict):
        source_fields["default_variant"] = requested_variant
    return updated
