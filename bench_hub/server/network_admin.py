"""Docker network/container administration helpers."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from docker.errors import NotFound

from bench_hub.server.launch_runtime import release_reserved_project_local_subnet
from bench_hub.server.server_state import (
    DOCKER_NETWORK,
    NETWORK_REMOVE_RETRY_INTERVAL_S,
    NETWORK_REMOVE_RETRY_TIMEOUT_S,
    get_docker_client,
)

logger = logging.getLogger(__name__)


def list_project_containers(project_name: str):
    """Return all containers (any state) for a docker compose project."""
    try:
        return get_docker_client().containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={project_name}"},
        )
    except Exception as e:
        logger.warning(f"[Docker] Failed to list containers for project {project_name}: {e}")
        return []


def summarize_project_containers(project_name: str, max_logs_tail: int = 40) -> List[dict]:
    summaries: List[dict] = []
    for c in list_project_containers(project_name):
        try:
            c.reload()
            state = (c.attrs or {}).get("State", {}) if hasattr(c, "attrs") else {}
            status = state.get("Status") or getattr(c, "status", None)
            exit_code = state.get("ExitCode")
            summary = {
                "name": c.name,
                "service": (c.labels or {}).get("com.docker.compose.service"),
                "status": status,
                "exit_code": exit_code,
                "error": state.get("Error"),
            }

            if status not in ("running",) and max_logs_tail > 0:
                try:
                    raw = c.logs(tail=max_logs_tail)
                    if isinstance(raw, (bytes, bytearray)):
                        summary["logs_tail"] = raw.decode("utf-8", errors="replace")
                    else:
                        summary["logs_tail"] = str(raw)
                except Exception:
                    pass

            if summary.get("logs_tail") and len(summary["logs_tail"]) > 4000:
                summary["logs_tail"] = summary["logs_tail"][-4000:]

            summaries.append(summary)
        except Exception as e:
            summaries.append({"name": getattr(c, "name", "<unknown>"), "error": f"failed to summarize: {e}"})
    return summaries


def resolve_service_inner_ips(project_name: str, network_name: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for container in list_project_containers(project_name):
        labels = getattr(container, "labels", {}) or {}
        service_name = labels.get("com.docker.compose.service")
        if not service_name:
            continue
        try:
            container.reload()
            networks = ((container.attrs or {}).get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
            network_info = networks.get(network_name, {}) or {}
            ip_address = str(network_info.get("IPAddress", "") or "").strip()
            if ip_address:
                mapping[service_name] = ip_address
        except Exception as e:
            logger.warning("Failed to resolve inner IP for %s/%s: %s", project_name, service_name, e)
    return mapping


def ensure_docker_network() -> None:
    """Claim exclusive ownership of DOCKER_NETWORK for this server process.

    Policy (option A, strict fail-fast):
      - Network does not exist         → create it.
      - Network exists and is empty    → leftover from previous crash; delete + recreate.
      - Network exists with containers → another server with the same
        CTF_NAMESPACE is (or was) running. Refuse to start.
    """
    client = get_docker_client()
    try:
        network = client.networks.get(DOCKER_NETWORK)
    except NotFound:
        logger.info(f"Creating network '{DOCKER_NETWORK}'")
        client.networks.create(DOCKER_NETWORK, driver="bridge")
        return

    try:
        network.reload()
    except Exception as exc:
        raise RuntimeError(
            f"Docker network '{DOCKER_NETWORK}' exists but inspect failed: {exc}. "
            f"Refusing to start."
        ) from exc

    containers = network.attrs.get("Containers", {}) or {}
    if containers:
        names = sorted((c or {}).get("Name", "<unknown>") for c in containers.values())
        raise RuntimeError(
            f"Docker network '{DOCKER_NETWORK}' already has {len(containers)} "
            f"attached container(s): {names}. Another Challenge server with "
            f"CTF_NAMESPACE may be running. Refusing to start.\n"
            f"If you are certain no other server is running, remove it manually: "
            f"docker network rm {DOCKER_NETWORK}"
        )

    logger.info(
        f"Docker network '{DOCKER_NETWORK}' exists but is empty "
        f"(leftover from previous run); removing and recreating."
    )
    try:
        network.remove()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to remove stale network '{DOCKER_NETWORK}': {exc}"
        ) from exc
    client.networks.create(DOCKER_NETWORK, driver="bridge")


def cleanup_orphan_networks() -> None:
    """Remove orphan Docker networks from previous server runs.

    Scans `ctfnet_*` (per-namespace agent home networks) and `ctf_*_runtime_*`
    (per-challenge runtime networks). A network is considered an orphan if it
    has zero active container endpoints. The current namespace's own
    DOCKER_NETWORK is always preserved.
    """
    client = get_docker_client()
    removed_ctfnet = 0
    removed_runtime = 0
    try:
        all_networks = client.networks.list()
    except Exception as exc:
        logger.warning(f"cleanup_orphan_networks: failed to list networks: {exc}")
        return

    for network in all_networks:
        name = getattr(network, "name", "") or ""
        if name == DOCKER_NETWORK:
            continue
        is_ctfnet = name.startswith("ctfnet_")
        is_runtime = name.startswith("ctf_") and "_runtime_" in name
        if not (is_ctfnet or is_runtime):
            continue
        try:
            network.reload()
            container_count = len(network.attrs.get("Containers", {}) or {})
        except NotFound:
            continue
        except Exception as exc:
            logger.warning(f"cleanup_orphan_networks: reload failed for {name}: {exc}")
            continue
        if container_count > 0:
            continue
        try:
            network.remove()
            if is_ctfnet:
                removed_ctfnet += 1
            else:
                removed_runtime += 1
            for cfg in (network.attrs.get("IPAM", {}).get("Config") or []):
                subnet = (cfg or {}).get("Subnet")
                if subnet:
                    try:
                        release_reserved_project_local_subnet(subnet)
                    except Exception:
                        pass
        except NotFound:
            continue
        except Exception as exc:
            logger.warning(f"cleanup_orphan_networks: failed to remove {name}: {exc}")

    if removed_ctfnet or removed_runtime:
        logger.info(
            f"cleanup_orphan_networks: removed {removed_ctfnet} ctfnet_* + "
            f"{removed_runtime} ctf_*_runtime_* orphan networks"
        )
    else:
        logger.info("cleanup_orphan_networks: no orphan networks found")


def _has_active_endpoints_error(error: Exception) -> bool:
    return "active endpoints" in str(error).lower()


def remove_network_with_retry(
    network: Any,
    *,
    timeout_s: float = NETWORK_REMOVE_RETRY_TIMEOUT_S,
    poll_interval_s: float = NETWORK_REMOVE_RETRY_INTERVAL_S,
) -> None:
    deadline = time.time() + max(timeout_s, 0.0)
    while True:
        try:
            network.remove()
            logger.info(f"Removed network: {network.name}")
            return
        except NotFound:
            logger.info("Network already removed: %s", getattr(network, "name", "<unknown>"))
            return
        except Exception as e:
            if not _has_active_endpoints_error(e):
                raise
            if time.time() >= deadline:
                raise
            logger.info(
                "Network %s still has active endpoints; waiting %.1fs before retry",
                getattr(network, "name", "<unknown>"),
                poll_interval_s,
            )
            time.sleep(poll_interval_s)


def remove_own_docker_network() -> None:
    """Force-remove the server-owned DOCKER_NETWORK on shutdown."""
    client = get_docker_client()
    try:
        network = client.networks.get(DOCKER_NETWORK)
    except NotFound:
        return
    except Exception as exc:
        logger.warning(f"Failed to look up {DOCKER_NETWORK} on shutdown: {exc}")
        return
    try:
        network.reload()
        for cid in list((network.attrs.get("Containers", {}) or {}).keys()):
            try:
                network.disconnect(cid, force=True)
            except Exception as exc:
                logger.warning(
                    f"Failed to force-disconnect {cid} from {DOCKER_NETWORK}: {exc}"
                )
    except Exception as exc:
        logger.warning(f"Failed to inspect {DOCKER_NETWORK} before removal: {exc}")
    try:
        network.remove()
        logger.info(f"Removed own docker network '{DOCKER_NETWORK}' on shutdown")
    except NotFound:
        pass
    except Exception as exc:
        logger.warning(f"Failed to remove {DOCKER_NETWORK} on shutdown: {exc}")
