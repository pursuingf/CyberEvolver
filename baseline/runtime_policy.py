"""Runtime isolation policy shared by baseline runners."""

from __future__ import annotations

from typing import Any, Dict, Mapping


NETWORK_PARALLEL_AGENTS = frozenset({"autopenbench", "vulnbot"})
BENCHMARK_NETWORK_AGENT_PAIRS = frozenset({("cy_agent", "cvebench"), ("t_agent", "cvebench")})
NETWORK_PARALLEL_BENCHMARKS = frozenset({"cvebench", "autopenbench"})


def runtime_args_for_agent(
    agent_name: str,
    chal_meta: Mapping[str, Any] | None = None,
) -> Dict[str, str]:
    """Return Challenge server runtime args for a baseline mini_cyberagent.

    Most legacy baselines use host/port aliases. AutoPenBench needs the agent
    container on the same Docker network as the target so upstream tools can
    scan and connect to in-benchmark addresses.
    """
    benchmark_family = ""
    if chal_meta:
        benchmark_family = str(
            chal_meta.get("benchmark_family") or chal_meta.get("benchmark") or ""
        ).lower()
    network_benchmark = benchmark_family in NETWORK_PARALLEL_BENCHMARKS
    network_pair = (agent_name, benchmark_family) in BENCHMARK_NETWORK_AGENT_PAIRS
    parallel_mode = "network" if agent_name in NETWORK_PARALLEL_AGENTS or network_benchmark or network_pair else "alias"
    return {"target_scope": "per_agent", "parallel_mode": parallel_mode}


def docker_config_for_challenge(
    docker_config: Mapping[str, Any] | None,
    chal_data: Mapping[str, Any],
) -> Dict[str, Any]:
    """Merge static Docker config with challenge runtime metadata.

    The Challenge server owns dynamic target networking. If it returns a runtime
    network, the agent container must join that network even when the YAML file
    still contains the legacy fallback network name.
    """
    resolved = dict(docker_config or {})
    runtime = chal_data.get("runtime") or {}
    if isinstance(runtime, Mapping):
        target_network = runtime.get("network_name")
        if target_network:
            resolved["network_name"] = target_network
    return resolved
