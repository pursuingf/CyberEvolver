from __future__ import annotations

from typing import Any

from bench_hub.adapters.autopenbench import AutoPenBenchAdapter
from bench_hub.adapters.challenge_json import ChallengeJsonAdapter
from bench_hub.adapters.registry import BenchmarkAdapterRegistry
from bench_hub.adapters.roots import normalize_benchmark_sources, resolve_configured_benchmark_root


def build_default_registry() -> BenchmarkAdapterRegistry:
    registry = BenchmarkAdapterRegistry()
    registry.register(ChallengeJsonAdapter())
    registry.register(AutoPenBenchAdapter())
    return registry


def resolve_benchmark_sources(config: Any) -> list[dict[str, Any]]:
    configured_sources = getattr(config, "benchmark_sources", None)
    if configured_sources:
        return normalize_benchmark_sources(list(configured_sources))

    return normalize_benchmark_sources(
        [
            {
                "adapter_kind": "challenge_json",
                "root": str(resolve_configured_benchmark_root(getattr(config, "benchmark_root", "./bench_hub/benchmarks"))),
            }
        ]
    )
