"""Challenge launch and cleanup workflow: docker compose up/down + post-start verification."""
from __future__ import annotations

import ipaddress
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from bench_hub.adapters.source_config import build_default_registry
from bench_hub.server.launch_runtime import (
    materialize_compose_runtime,
    release_reserved_project_local_subnet,
)
from bench_hub.server.network_admin import (
    list_project_containers,
    remove_network_with_retry,
    resolve_service_inner_ips,
    summarize_project_containers,
)
from bench_hub.server.health_probes import (
    is_instance_healthy,
    wait_for_containers_running,
    wait_for_inner_services_ready,
    wait_for_services_healthy,
)
from bench_hub.server.schemas import LaunchResponse, ServiceInfo
from bench_hub.server.server_state import (
    COMPOSE_UP_TIMEOUT_S,
    CTF_NAMESPACE,
    DOCKER_NETWORK,
    HOST_IP,
    STARTUP_POLL_INTERVAL_S,
    STARTUP_TIMEOUT_S,
    challenge_locks,
    find_free_port,
    get_docker_client,
    get_running_instance,
    load_all_challenges,
    pop_running_instance,
    release_allocated_port,
    set_running_instance,
    update_running_instance,
)
from common.utils.runtime_policy import normalize_target_scope, resolve_target_scope

logger = logging.getLogger(__name__)


def pydantic_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def parse_internal_port(port_def: Any) -> Optional[int]:
    try:
        if isinstance(port_def, int):
            return port_def
        if isinstance(port_def, str):
            return int(port_def.split(':')[-1].split('/')[0])
        return None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def ensure_docker_cli_config_dir(env: Dict[str, str]) -> None:
    docker_config = env.get("DOCKER_CONFIG", "").strip()
    if not docker_config:
        docker_config = f"/tmp/ctf-docker-config-{CTF_NAMESPACE}"
        env["DOCKER_CONFIG"] = docker_config
    Path(docker_config).mkdir(parents=True, exist_ok=True)


def load_env_file_vars(env_file: str | Path | None) -> Dict[str, str]:
    if not env_file:
        return {}
    path = Path(env_file)
    if not path.exists():
        return {}

    result: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _cleanup_instance_impl(chal_id: str, run_id: Optional[str] = None):
    """Force-clean a project's containers, networks, volumes, and tracked allocations."""
    instance_key = run_id or chal_id
    update_running_instance(instance_key, lifecycle_state="cleanup")
    existing_instance = get_running_instance(instance_key)
    safe_id = chal_id.replace('-', '_').lower()
    project_name = (
        existing_instance.get("project_name")
        if existing_instance is not None and existing_instance.get("project_name")
        else f"ctf_{CTF_NAMESPACE}_{safe_id}_runtime"
    )

    logger.info(f"Cleaning up project: {project_name} ...")

    try:
        containers = get_docker_client().containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={project_name}"}
        )
        for container in containers:
            try:
                try:
                    container.kill()
                except Exception:
                    pass
                container.remove(force=True, v=True)
                logger.info(f"Removed container: {container.name}")
            except Exception as e:
                logger.warning(f"Failed to remove container {container.name}: {e}")
    except Exception as e:
        logger.error(f"Error listing/removing containers for {project_name}: {e}")

    try:
        networks = get_docker_client().networks.list(
            filters={"label": f"com.docker.compose.project={project_name}"}
        )
        for network in networks:
            if network.name == DOCKER_NETWORK:
                continue
            try:
                remove_network_with_retry(network)
            except Exception as e:
                logger.warning(f"Failed to remove network {network.name}: {e}")
    except Exception as e:
        logger.error(f"Error cleaning networks: {e}")

    try:
        volumes = get_docker_client().volumes.list(
            filters={"label": f"com.docker.compose.project={project_name}"}
        )
        for volume in volumes:
            try:
                volume.remove(force=True)
                logger.info(f"Removed volume: {volume.name}")
            except Exception as e:
                logger.warning(f"Failed to remove volume {volume.name}: {e}")
    except Exception as e:
        logger.error(f"Error cleaning volumes for {project_name}: {e}")

    instance = pop_running_instance(instance_key)
    if instance is not None:
        release_reserved_project_local_subnet(instance.get("network_subnet"))
        for extra_subnet in (instance.get("all_subnets") or []):
            release_reserved_project_local_subnet(extra_subnet)
        for port in (instance.get("external_ports") or {}).values():
            if port is not None:
                release_allocated_port(int(port))
        compose_path = instance.get("compose_path")
        if compose_path:
            try:
                Path(compose_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to remove runtime compose {compose_path}: {e}")
        logger.info(f"Instance {instance_key} removed from memory.")


def cleanup_instance(chal_id: str, run_id: Optional[str] = None):
    with challenge_locks.get_lock(chal_id):
        _cleanup_instance_impl(chal_id, run_id=run_id)


def _reused_launch_response(chal_id: str, instance: dict) -> LaunchResponse:
    debug = instance.get("debug", {}) or {}
    network_debug = debug.get("network", {}) or {}
    return LaunchResponse(
        status="reused",
        chal_id=chal_id,
        run_id=instance.get("run_id"),
        project_name=instance["project_name"],
        network_name=instance.get("network_name"),
        network_subnet=instance.get("network_subnet") or network_debug.get("subnet"),
        network_gateway=instance.get("network_gateway") or network_debug.get("gateway"),
        scoring=instance.get("scoring", {}),
        debug=debug,
        services=instance["services"],
    )


def resolve_parallel_mode(meta: Dict[str, Any], requested_parallel_mode: Optional[str]) -> str:
    requested = str(requested_parallel_mode or "").strip().lower()
    if requested:
        if requested not in {"network", "alias"}:
            raise HTTPException(status_code=400, detail=f"Unsupported parallel_mode: {requested_parallel_mode}")
        return requested
    if str(meta.get("benchmark_family", "") or "").lower() in {"cvebench", "autopenbench"}:
        return "network"
    return "alias"


def resolve_server_target_scope(meta: Dict[str, Any], requested_target_scope: Optional[str]) -> str:
    requested = normalize_target_scope(requested_target_scope)
    if requested:
        return requested
    return resolve_target_scope(chal_data=meta, runtime_args={})


def build_network_debug(project_name: str, network_name: str, parallel_mode: str) -> Dict[str, Any]:
    debug: Dict[str, Any] = {
        "parallel_mode": parallel_mode,
        "network": {
            "name": network_name,
        },
    }
    try:
        network = get_docker_client().networks.get(network_name)
        attrs = getattr(network, "attrs", {}) or {}
        network_block = debug["network"]
        network_block["driver"] = attrs.get("Driver")

        ipam_config = (((attrs.get("IPAM", {}) or {}).get("Config") or [{}])[0] or {})
        subnet = str(ipam_config.get("Subnet", "") or "").strip() or None
        gateway = str(ipam_config.get("Gateway", "") or "").strip() or None
        if subnet:
            network_block["subnet"] = subnet
        if gateway:
            network_block["gateway"] = gateway
        if subnet:
            parsed = ipaddress.ip_network(subnet, strict=False)
            network_block["total_addresses"] = parsed.num_addresses
            network_block["usable_addresses"] = max(parsed.num_addresses - 2, 0)

        status_subnets = ((((attrs.get("Status", {}) or {}).get("IPAM", {}) or {}).get("Subnets", {}) or {}))
        if subnet and subnet in status_subnets:
            subnet_status = status_subnets[subnet] or {}
            network_block["ips_in_use"] = subnet_status.get("IPsInUse")
            network_block["dynamic_ips_available"] = subnet_status.get("DynamicIPsAvailable")

        services: List[Dict[str, Any]] = []
        for container in list_project_containers(project_name):
            labels = getattr(container, "labels", {}) or {}
            service_name = labels.get("com.docker.compose.service")
            if not service_name:
                continue
            try:
                container.reload()
                networks = (((container.attrs or {}).get("NetworkSettings", {}) or {}).get("Networks", {}) or {})
                network_info = networks.get(network_name)
                if not network_info:
                    continue
                ipv4 = str(network_info.get("IPAddress", "") or "").strip() or None
                mac_address = str(network_info.get("MacAddress", "") or "").strip() or None
                services.append(
                    {
                        "service_name": service_name,
                        "container_name": getattr(container, "name", None),
                        "ipv4": ipv4,
                        "mac_address": mac_address,
                    }
                )
            except Exception as exc:
                services.append({"service_name": service_name, "error": str(exc)})

        services.sort(key=lambda item: item.get("service_name", ""))
        network_block["services"] = services
    except Exception as exc:
        debug["network"]["error"] = str(exc)
    return debug


def _launch_challenge_impl(
    chal_id: str,
    force_recreate: bool,
    parallel_mode: Optional[str] = None,
    target_scope: Optional[str] = None,
) -> LaunchResponse:
    """Launch (or reuse, or rebuild) the runtime instance for a challenge.

    - Existing healthy instance + not forced → reuse.
    - Existing instance unhealthy or force_recreate → cleanup and rebuild (port reuse).
    - First launch → allocate fresh ports.
    """
    challenges = load_all_challenges()
    if chal_id not in challenges:
        raise HTTPException(status_code=404, detail="Challenge not found")

    meta = challenges[chal_id]
    effective_parallel_mode = resolve_parallel_mode(meta, parallel_mode)
    effective_target_scope = resolve_server_target_scope(meta, target_scope)
    allow_parallel_runs = effective_target_scope == "per_agent"

    existing_instance = None if allow_parallel_runs else get_running_instance(chal_id)
    should_recreate = False
    reason = ""

    if existing_instance:
        if force_recreate:
            should_recreate = True
            reason = "force_recreate=True"
        elif not is_instance_healthy(chal_id):
            should_recreate = True
            reason = "instance unhealthy (port(s) closed)"
        else:
            logger.info(f"Reusing healthy instance for {chal_id}")
            return _reused_launch_response(chal_id, existing_instance)

    if should_recreate:
        logger.info(f"Recreating {chal_id} because: {reason}. Cleaning up old instance...")
        _cleanup_instance_impl(chal_id)

    adapter = build_default_registry().get(meta["adapter_kind"])
    launch_spec = adapter.build_launch_spec(meta)
    launch_spec.runtime_patches["parallel_mode"] = effective_parallel_mode
    launch_spec.runtime_patches["target_scope"] = effective_target_scope
    chal_path = Path(launch_spec.working_directory)

    if launch_spec.mode == "static":
        return LaunchResponse(status="static", chal_id=chal_id, run_id=chal_id)

    safe_id = chal_id.replace('-', '_').lower()
    run_id = chal_id
    if allow_parallel_runs:
        run_id = f"{safe_id}_{uuid.uuid4().hex[:8]}"
        project_name = f"ctf_{CTF_NAMESPACE}_{safe_id}_{run_id.split('_')[-1]}_runtime"
        runtime_compose_filename = f"docker-compose.runtime.{CTF_NAMESPACE}.{run_id.split('_')[-1]}.yml"
    else:
        project_name = f"ctf_{CTF_NAMESPACE}_{safe_id}_runtime"
        runtime_compose_filename = f"docker-compose.runtime.{CTF_NAMESPACE}.yml"
    runtime_compose_path = chal_path / runtime_compose_filename

    saved_existing_instance = existing_instance if should_recreate else None
    runtime_plan = materialize_compose_runtime(
        spec=launch_spec,
        project_name=project_name,
        docker_network=DOCKER_NETWORK,
        host_ip=HOST_IP,
        runtime_compose_path=runtime_compose_path,
        find_free_port_fn=find_free_port,
        existing_external_ports=(saved_existing_instance or {}).get("external_ports"),
    )
    public_service_names = runtime_plan.public_service_names
    final_services = [ServiceInfo(**item) for item in runtime_plan.services]
    scoring = dict((meta.get("source_fields", {}) or {}).get("runtime_scoring", {}) or {})
    runtime_network_name = runtime_plan.agent_network_name or DOCKER_NETWORK

    # Collect project-local subnets BEFORE container start so error paths can release them
    # even when the instance is not yet registered.
    allocated_subnets: list[str] = []
    _runtime_networks = (runtime_plan.config.get("networks", {}) or {})
    for _net_name, _net_cfg in _runtime_networks.items():
        if not isinstance(_net_cfg, dict):
            continue
        if _net_cfg.get("external"):
            continue
        _ipam = _net_cfg.get("ipam", {}) or {}
        _ipam_cfgs = _ipam.get("config", []) or []
        for _ipam_entry in _ipam_cfgs:
            _subnet = str((_ipam_entry or {}).get("subnet", "") or "").strip()
            if _subnet:
                allocated_subnets.append(_subnet)

    def _release_allocated_subnets_on_error():
        for _subnet in allocated_subnets:
            try:
                release_reserved_project_local_subnet(_subnet)
            except Exception as _exc:
                logger.warning(f"Failed to release subnet {_subnet} on error path: {_exc}")

    logger.info(f"Launching {project_name} (recreate={should_recreate})...")
    env = os.environ.copy()
    # BuildKit/bake can hang on some challenge contexts in this environment; default to classic builder.
    env["DOCKER_BUILDKIT"] = os.getenv("CTF_DOCKER_BUILDKIT", "0")
    env.update(launch_spec.runtime_patches.get("compose_env", {}) or {})
    env.update(load_env_file_vars(launch_spec.runtime_patches.get("env_file")))
    ensure_docker_cli_config_dir(env)
    cmd = [
        "docker", "compose", "-p", project_name,
        "-f", runtime_compose_filename, "up", "-d", "--build", "--force-recreate"
    ]
    try:
        res = subprocess.run(cmd, cwd=chal_path, env=env, capture_output=True, text=True, timeout=COMPOSE_UP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.error(f"Compose up timed out after {COMPOSE_UP_TIMEOUT_S}s for {project_name}")
        _release_allocated_subnets_on_error()
        raise HTTPException(500, detail=f"Docker compose up timed out after {COMPOSE_UP_TIMEOUT_S}s")
    if res.returncode != 0:
        logger.error(f"Up failed:\n{res.stderr}")
        _release_allocated_subnets_on_error()
        raise HTTPException(500, detail=f"Docker up failed: {res.stderr}")

    # docker compose can return 0 even if containers exit immediately — verify before reporting success.
    try:
        containers_err = wait_for_containers_running(
            project_name=project_name,
            required_services=public_service_names if public_service_names else None,
            timeout_s=STARTUP_TIMEOUT_S,
            poll_interval_s=STARTUP_POLL_INTERVAL_S,
        )
        if containers_err:
            raise RuntimeError(containers_err)

        health_err = wait_for_services_healthy(
            project_name=project_name,
            required_services=public_service_names if public_service_names else None,
            timeout_s=STARTUP_TIMEOUT_S,
            poll_interval_s=STARTUP_POLL_INTERVAL_S,
        )
        if health_err:
            raise RuntimeError(health_err)

        service_inner_ips = resolve_service_inner_ips(project_name, runtime_network_name)
        for service in final_services:
            service.inner_ip = service_inner_ips.get(service.service_name)
            if (
                str(meta.get("category", "") or "").lower() == "web"
                and service.inner_port is not None
                and str(service.protocol or "tcp").lower() == "tcp"
            ):
                service.protocol = "http"

        inner_err = wait_for_inner_services_ready(
            services=final_services,
            network_name=runtime_network_name,
            timeout_s=STARTUP_TIMEOUT_S,
            poll_interval_s=STARTUP_POLL_INTERVAL_S,
        )
        if inner_err:
            raise RuntimeError(inner_err)
    except Exception as e:
        details = {
            "error": str(e),
            "project_name": project_name,
            "public_services": public_service_names,
            "services": [pydantic_to_dict(svc) for svc in final_services],
            "containers": summarize_project_containers(project_name),
        }
        logger.error(f"[LaunchVerify] Instance {chal_id} failed to become ready: {details}")
        # cleanup_instance is a no-op here since the instance has not been registered yet,
        # so we MUST release subnets explicitly.
        cleanup_instance(chal_id)
        _release_allocated_subnets_on_error()
        raise HTTPException(status_code=500, detail=details)

    debug = build_network_debug(
        project_name=project_name,
        network_name=runtime_network_name,
        parallel_mode=effective_parallel_mode,
    )
    debug["target_scope"] = effective_target_scope
    network_debug = debug.get("network", {}) or {}
    network_subnet = network_debug.get("subnet")
    network_gateway = network_debug.get("gateway")

    all_subnets = []
    runtime_networks = (runtime_plan.config.get("networks", {}) or {})
    for _net_name, _net_cfg in runtime_networks.items():
        if not isinstance(_net_cfg, dict):
            continue
        if _net_cfg.get("external"):
            continue
        _ipam = _net_cfg.get("ipam", {}) or {}
        _ipam_cfgs = _ipam.get("config", []) or []
        for _ipam_entry in _ipam_cfgs:
            _subnet = str((_ipam_entry or {}).get("subnet", "") or "").strip()
            if _subnet:
                all_subnets.append(_subnet)

    set_running_instance(run_id, {
        "chal_id": chal_id,
        "run_id": run_id,
        "project_name": project_name,
        "network_name": runtime_network_name,
        "network_subnet": network_subnet,
        "network_gateway": network_gateway,
        "scoring": scoring,
        "debug": debug,
        "compose_path": runtime_compose_path,
        "services": final_services,
        "public_services": public_service_names,
        "external_ports": runtime_plan.external_ports,
        "lifecycle_state": "running",
        "target_scope": effective_target_scope,
        "all_subnets": all_subnets,
    })

    status = "launched" if not should_recreate else "recreated"
    return LaunchResponse(
        status=status,
        chal_id=chal_id,
        run_id=run_id,
        project_name=project_name,
        network_name=runtime_network_name,
        network_subnet=network_subnet,
        network_gateway=network_gateway,
        scoring=scoring,
        debug=debug,
        services=final_services
    )
