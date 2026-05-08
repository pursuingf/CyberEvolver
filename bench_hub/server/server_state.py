"""Process-wide state for the challenge server: env config, docker client, instance registry, ports, caches."""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
import yaml

from bench_hub.adapters.roots import normalize_benchmark_sources, resolve_repo_benchmark_root
from bench_hub.adapters.source_config import build_default_registry
from bench_hub.server.runtime_guards import ChallengeLockRegistry, ChallengeRecoveryCoordinator

logger = logging.getLogger(__name__)

# ============ Paths & env-driven config ============
BASE_DIR = Path(__file__).parent.resolve()
BENCHMARK_ROOT = resolve_repo_benchmark_root(BASE_DIR.parent)

HOST_IP = os.getenv("CTF_HOST_IP") or os.getenv("SERVICE_HOST_IP") or "127.0.0.1"
CTF_NAMESPACE = os.getenv("CTF_NAMESPACE", "default")
DOCKER_NETWORK = f"ctfnet_{CTF_NAMESPACE}"
STARTUP_TIMEOUT_S = float(os.getenv("CTF_STARTUP_TIMEOUT_S", "120"))
STARTUP_POLL_INTERVAL_S = float(os.getenv("CTF_STARTUP_POLL_INTERVAL_S", "1.0"))
PORT_OPEN_STABILITY_CHECKS = max(1, int(os.getenv("CTF_PORT_OPEN_STABILITY_CHECKS", "2")))
HEALTH_TIMEOUT_S = float(os.getenv("CTF_HEALTH_TIMEOUT_S", "2.0"))
INSTANCE_HEALTH_TIMEOUT_S = float(
    os.getenv("CTF_INSTANCE_HEALTH_TIMEOUT_S", str(max(10.0, HEALTH_TIMEOUT_S)))
)
HEALTH_POLL_INTERVAL_S = float(os.getenv("CTF_HEALTH_POLL_INTERVAL_S", "0.5"))
NETWORK_REMOVE_RETRY_TIMEOUT_S = float(os.getenv("CTF_NETWORK_REMOVE_RETRY_TIMEOUT_S", "60"))
NETWORK_REMOVE_RETRY_INTERVAL_S = float(os.getenv("CTF_NETWORK_REMOVE_RETRY_INTERVAL_S", "10"))
COMPOSE_UP_TIMEOUT_S = float(os.getenv("CTF_COMPOSE_UP_TIMEOUT_S", "300"))

# ============ Running instance registry ============
running_instances: Dict[str, dict] = {}
running_instances_lock = threading.RLock()
challenge_locks = ChallengeLockRegistry()
recovery_coordinator = ChallengeRecoveryCoordinator(
    recent_recovery_window_s=float(os.getenv("CTF_RECENT_RECOVERY_WINDOW_S", "5.0"))
)

# ============ Docker client (auto-reconnect) ============
_docker_client_lock = threading.Lock()
_docker_client: Optional[docker.DockerClient] = None


def get_docker_client() -> docker.DockerClient:
    """Return a shared Docker client, reconnecting if the daemon was restarted."""
    global _docker_client
    with _docker_client_lock:
        if _docker_client is None:
            _docker_client = docker.from_env()
        else:
            try:
                _docker_client.ping()
            except Exception:
                logger.warning("[Docker] Client stale, reconnecting...")
                try:
                    _docker_client.close()
                except Exception:
                    pass
                _docker_client = docker.from_env()
        return _docker_client


# ============ Port allocation (race-guarded) ============
_allocated_ports_lock = threading.Lock()
_allocated_ports: set[int] = set()


def find_free_port() -> int:
    """Allocate a free port, guarded against concurrent races."""
    with _allocated_ports_lock:
        for _ in range(50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', 0))
                s.listen(1)
                port = s.getsockname()[1]
            if port not in _allocated_ports:
                _allocated_ports.add(port)
                return port
    raise RuntimeError("Unable to allocate a unique free port after 50 attempts")


def release_allocated_port(port: int) -> None:
    with _allocated_ports_lock:
        _allocated_ports.discard(port)


# ============ Instance registry helpers ============
def get_running_instance(chal_id: str) -> Optional[dict]:
    with running_instances_lock:
        instance = running_instances.get(chal_id)
        if instance is not None:
            return instance
        matches = [info for info in running_instances.values() if info.get("chal_id") == chal_id]
        if not matches:
            return None
        return matches[-1]


def set_running_instance(chal_id: str, value: dict) -> None:
    with running_instances_lock:
        running_instances[chal_id] = value


def update_running_instance(chal_id: str, **updates: Any) -> Optional[dict]:
    with running_instances_lock:
        instance = running_instances.get(chal_id)
        if instance is None:
            matching_keys = [key for key, info in running_instances.items() if info.get("chal_id") == chal_id]
            if matching_keys:
                instance = running_instances.get(matching_keys[-1])
        if instance is None:
            return None
        instance.update(updates)
        return instance


def pop_running_instance(chal_id: str) -> Optional[dict]:
    with running_instances_lock:
        instance = running_instances.pop(chal_id, None)
        if instance is not None:
            return instance
        matching_keys = [key for key, info in running_instances.items() if info.get("chal_id") == chal_id]
        if not matching_keys:
            return None
        return running_instances.pop(matching_keys[-1], None)


def snapshot_running_instance_ids() -> List[str]:
    with running_instances_lock:
        return list(running_instances.keys())


# ============ Challenge metadata cache ============
_challenge_cache: Optional[Dict[str, dict]] = None
_challenge_cache_lock = threading.Lock()


def load_all_challenges() -> Dict[str, dict]:
    """Return cached challenge metadata. Loaded once at first call."""
    global _challenge_cache
    with _challenge_cache_lock:
        if _challenge_cache is not None:
            return _challenge_cache
    registry = build_default_registry()
    try:
        result = registry.discover_all(load_benchmark_sources())
    except Exception as e:
        logger.error(f"Error loading benchmark sources: {e}")
        raise
    with _challenge_cache_lock:
        if _challenge_cache is None:
            _challenge_cache = result
        return _challenge_cache


def invalidate_challenge_cache() -> None:
    global _challenge_cache
    with _challenge_cache_lock:
        _challenge_cache = None


def load_benchmark_sources() -> List[dict]:
    raw_sources = os.getenv("CTF_BENCHMARK_SOURCES_JSON", "").strip()
    if raw_sources:
        return json.loads(raw_sources)

    config_path = os.getenv("CTF_BENCHMARK_SOURCES_FILE", "").strip()
    if config_path:
        return _load_benchmark_sources_file(Path(config_path))
    return normalize_benchmark_sources(
        [
            {
                "adapter_kind": "challenge_json",
                "root": str(BENCHMARK_ROOT),
            }
        ]
    )


def _load_benchmark_sources_file(config_path: Path) -> List[dict]:
    with open(config_path, "r", encoding="utf-8") as handle:
        if config_path.suffix.lower() == ".json":
            payload = json.load(handle)
        else:
            payload = yaml.safe_load(handle) or {}

    if isinstance(payload, list):
        return normalize_benchmark_sources(payload)
    if isinstance(payload, dict):
        if isinstance(payload.get("benchmark_sources"), list):
            return normalize_benchmark_sources(payload["benchmark_sources"])
        challenge_client = payload.get("challenge_client", {}) or {}
        if isinstance(challenge_client.get("benchmark_sources"), list):
            return normalize_benchmark_sources(challenge_client["benchmark_sources"])

    raise ValueError(f"Unsupported benchmark sources config structure: {config_path}")
