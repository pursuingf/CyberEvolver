#!/usr/bin/env python3
try:
    from bench_hub.server.script_bootstrap import ensure_repo_root_on_path
except ModuleNotFoundError:
    from script_bootstrap import ensure_repo_root_on_path

ensure_repo_root_on_path(__file__)

import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import docker
import uvicorn
import yaml
from docker.errors import NotFound
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from bench_hub.adapters.roots import normalize_benchmark_sources, resolve_repo_benchmark_root
from bench_hub.adapters.source_config import build_default_registry

try:
    from bench_hub.server.launch_runtime import materialize_compose_runtime
    from bench_hub.server.runtime_guards import TargetLockRegistry, TargetRecoveryCoordinator
except ModuleNotFoundError:
    from launch_runtime import materialize_compose_runtime
    from runtime_guards import TargetLockRegistry, TargetRecoveryCoordinator


BASE_DIR = Path(__file__).parent.resolve()
BENCHMARK_ROOT = resolve_repo_benchmark_root(BASE_DIR.parent)

SERVICE_HOST_IP = os.getenv("CTF_HOST_IP") or os.getenv("SERVICE_HOST_IP") or "127.0.0.1"
DEFAULT_NAMESPACE = "default"
INSTANCE_STARTUP_TIMEOUT_S = float(os.getenv("CTF_STARTUP_TIMEOUT_S", "45"))
INSTANCE_STARTUP_POLL_INTERVAL_S = float(os.getenv("CTF_STARTUP_POLL_INTERVAL_S", "1.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

runtime_instances: Dict[str, dict] = {}
runtime_instances_lock = threading.RLock()
target_locks = TargetLockRegistry()
target_recovery_coordinator = TargetRecoveryCoordinator(
    recent_recovery_window_s=float(os.getenv("CTF_RECENT_RECOVERY_WINDOW_S", "5.0"))
)

client = docker.from_env()
app = FastAPI(title="Target Runtime Manager")


class TargetServiceInfo(BaseModel):
    service_name: str
    alias: str
    ip: str
    internal_port: Optional[int]
    external_port: Optional[int]


class LaunchTargetResponse(BaseModel):
    status: str
    target_id: str
    namespace: str
    project_name: Optional[str] = None
    services: List[TargetServiceInfo] = Field(default_factory=list)


class StopTargetResponse(BaseModel):
    status: str
    target_id: str
    namespace: str
    message: str


ServiceInfo = TargetServiceInfo
LaunchResponse = LaunchTargetResponse
StopResponse = StopTargetResponse


def normalize_namespace(namespace: str | None) -> str:
    candidate = (namespace or "").strip()
    return candidate or DEFAULT_NAMESPACE


def _slugify_with_separator(value: str, separator: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", separator, value.strip().lower())
    slug = re.sub(rf"{re.escape(separator)}+", separator, slug)
    slug = slug.strip(separator)
    return slug or DEFAULT_NAMESPACE


def build_runtime_key(target_id: str, namespace: str | None) -> str:
    return f"{normalize_namespace(namespace)}::{target_id}"


def split_runtime_key(runtime_key: str) -> Tuple[str, str]:
    namespace, _, target_id = runtime_key.partition("::")
    if not target_id:
        raise ValueError(f"Invalid runtime key: {runtime_key}")
    return target_id, namespace


def build_project_name(target_id: str, namespace: str | None) -> str:
    safe_namespace = _slugify_with_separator(normalize_namespace(namespace), "_")
    safe_target_id = _slugify_with_separator(target_id, "_")
    return f"target_{safe_namespace}_{safe_target_id}_runtime"


def build_runtime_compose_filename(namespace: str | None) -> str:
    safe_namespace = _slugify_with_separator(normalize_namespace(namespace), "-")
    return f"docker-compose.runtime.{safe_namespace}.yml"


def build_docker_network_name(namespace: str | None) -> str:
    safe_namespace = _slugify_with_separator(normalize_namespace(namespace), "_")
    return f"ctfnet_{safe_namespace}"


def build_docker_config_dir(namespace: str | None) -> str:
    safe_namespace = _slugify_with_separator(normalize_namespace(namespace), "_")
    return f"/tmp/ctf-docker-config-{safe_namespace}"


def get_runtime_instance(target_id: str, namespace: str | None) -> Optional[dict]:
    runtime_key = build_runtime_key(target_id, namespace)
    with runtime_instances_lock:
        return runtime_instances.get(runtime_key)


def set_runtime_instance(target_id: str, namespace: str | None, value: dict) -> None:
    runtime_key = build_runtime_key(target_id, namespace)
    with runtime_instances_lock:
        runtime_instances[runtime_key] = value


def pop_runtime_instance(target_id: str, namespace: str | None) -> Optional[dict]:
    runtime_key = build_runtime_key(target_id, namespace)
    with runtime_instances_lock:
        return runtime_instances.pop(runtime_key, None)


def snapshot_runtime_instance_keys() -> List[str]:
    with runtime_instances_lock:
        return list(runtime_instances.keys())


def clear_runtime_instances() -> None:
    with runtime_instances_lock:
        runtime_instances.clear()


def get_running_instance(chal_id: str) -> Optional[dict]:
    return get_runtime_instance(chal_id, DEFAULT_NAMESPACE)


def set_running_instance(chal_id: str, value: dict) -> None:
    set_runtime_instance(chal_id, DEFAULT_NAMESPACE, value)


def pop_running_instance(chal_id: str) -> Optional[dict]:
    return pop_runtime_instance(chal_id, DEFAULT_NAMESPACE)


def snapshot_running_instance_ids() -> List[str]:
    current_ids: List[str] = []
    for runtime_key in snapshot_runtime_instance_keys():
        target_id, namespace = split_runtime_key(runtime_key)
        if namespace == DEFAULT_NAMESPACE:
            current_ids.append(target_id)
    return current_ids


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        sock.listen(1)
        return sock.getsockname()[1]


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def list_project_containers(project_name: str):
    try:
        return client.containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={project_name}"},
        )
    except Exception as exc:
        logger.warning("[Docker] Failed to list containers for project %s: %s", project_name, exc)
        return []


def summarize_project_containers(project_name: str, max_logs_tail: int = 40) -> List[dict]:
    summaries: List[dict] = []
    for container in list_project_containers(project_name):
        try:
            container.reload()
            state = (container.attrs or {}).get("State", {}) if hasattr(container, "attrs") else {}
            status = state.get("Status") or getattr(container, "status", None)
            summary = {
                "name": container.name,
                "service": (container.labels or {}).get("com.docker.compose.service"),
                "status": status,
                "exit_code": state.get("ExitCode"),
                "error": state.get("Error"),
            }

            if status not in ("running",) and max_logs_tail > 0:
                try:
                    raw_logs = container.logs(tail=max_logs_tail)
                    if isinstance(raw_logs, (bytes, bytearray)):
                        summary["logs_tail"] = raw_logs.decode("utf-8", errors="replace")
                    else:
                        summary["logs_tail"] = str(raw_logs)
                except Exception:
                    pass

            if summary.get("logs_tail") and len(summary["logs_tail"]) > 4000:
                summary["logs_tail"] = summary["logs_tail"][-4000:]

            summaries.append(summary)
        except Exception as exc:
            summaries.append(
                {
                    "name": getattr(container, "name", "<unknown>"),
                    "error": f"failed to summarize: {exc}",
                }
            )
    return summaries


def wait_for_containers_running(
    project_name: str,
    required_services: Optional[List[str]],
    timeout_s: float,
    poll_interval_s: float,
) -> Optional[str]:
    deadline = time.time() + timeout_s
    required = set(required_services or [])

    while time.time() < deadline:
        containers = list_project_containers(project_name)
        if not containers:
            time.sleep(poll_interval_s)
            continue

        if not required:
            for container in containers:
                try:
                    container.reload()
                    state = (container.attrs or {}).get("State", {})
                    if state.get("Status") == "running":
                        return None
                except Exception:
                    continue
            time.sleep(poll_interval_s)
            continue

        running_by_service: Dict[str, bool] = {service_name: False for service_name in required}
        seen_by_service: Dict[str, bool] = {service_name: False for service_name in required}

        for container in containers:
            labels = container.labels or {}
            service_name = labels.get("com.docker.compose.service")
            if service_name not in required:
                continue
            seen_by_service[service_name] = True
            try:
                container.reload()
                state = (container.attrs or {}).get("State", {})
                if state.get("Status") == "running":
                    running_by_service[service_name] = True
            except Exception:
                pass

        missing = sorted(service_name for service_name, seen in seen_by_service.items() if not seen)
        not_running = sorted(service_name for service_name, ok in running_by_service.items() if not ok)
        if not missing and not not_running:
            return None

        time.sleep(poll_interval_s)

    if required:
        return f"timeout waiting for services to run: {sorted(required)}"
    return "timeout waiting for any container to run"


def wait_for_ports_open(
    services: List[TargetServiceInfo],
    timeout_s: float,
    poll_interval_s: float,
) -> Optional[str]:
    pending: Dict[str, int] = {
        service.service_name: int(service.external_port)
        for service in services
        if service.external_port is not None
    }
    if not pending:
        return None

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        opened_services = []
        for service_name, port in pending.items():
            if is_port_open("127.0.0.1", port, timeout=2.0):
                opened_services.append(service_name)
        for service_name in opened_services:
            pending.pop(service_name, None)

        if not pending:
            return None
        time.sleep(poll_interval_s)

    parts = [f"{service_name}:{port}" for service_name, port in sorted(pending.items())]
    return f"timeout waiting for ports to open: {', '.join(parts)}"


def pydantic_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def load_all_targets() -> Dict[str, dict]:
    registry = build_default_registry()
    try:
        return registry.discover_all(load_benchmark_sources())
    except Exception as exc:
        logger.error("Error loading benchmark sources: %s", exc)
        raise


def load_all_challenges() -> Dict[str, dict]:
    return load_all_targets()


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


def ensure_docker_network(namespace: str | None) -> str:
    docker_network = build_docker_network_name(namespace)
    try:
        client.networks.get(docker_network)
    except NotFound:
        logger.info("Creating network '%s'", docker_network)
        client.networks.create(docker_network, driver="bridge")
    return docker_network


def parse_internal_port(port_def: Any) -> Optional[int]:
    try:
        if isinstance(port_def, int):
            return port_def
        if isinstance(port_def, str):
            return int(port_def.split(":")[-1].split("/")[0])
        return None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def ensure_docker_cli_config_dir(env: Dict[str, str], namespace: str | None) -> None:
    docker_config = env.get("DOCKER_CONFIG", "").strip()
    if not docker_config:
        docker_config = build_docker_config_dir(namespace)
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


def strip_cve_identifier_env(env_vars: Dict[str, str]) -> Dict[str, str]:
    blocked = {"CVE", "CVE_LOWER"}
    return {key: value for key, value in env_vars.items() if key not in blocked}


def is_runtime_instance_healthy(target_id: str, namespace: str | None) -> bool:
    info = get_runtime_instance(target_id, namespace)
    if info is None:
        return False

    project_name = info.get("project_name")
    services = info.get("services", [])
    runtime_key = build_runtime_key(target_id, namespace)

    if not services:
        if not project_name:
            logger.warning("[HealthCheck] No services and no project_name recorded for %s", runtime_key)
            return False
        containers = list_project_containers(project_name)
        if not containers:
            logger.warning("[HealthCheck] No containers found for %s (%s)", runtime_key, project_name)
            return False
        for container in containers:
            try:
                container.reload()
                state = (container.attrs or {}).get("State", {})
                if state.get("Status") == "running":
                    return True
            except Exception:
                continue
        logger.warning("[HealthCheck] Containers exist but none running for %s (%s)", runtime_key, project_name)
        return False

    all_healthy = True
    for service in services:
        if service.external_port is None:
            continue
        if not is_port_open("127.0.0.1", service.external_port, timeout=2.0):
            logger.warning(
                "[HealthCheck] Port %s (service %s) is NOT open for %s",
                service.external_port,
                service.service_name,
                runtime_key,
            )
            all_healthy = False
        else:
            logger.debug(
                "[HealthCheck] Port %s (service %s) is open for %s",
                service.external_port,
                service.service_name,
                runtime_key,
            )

    return all_healthy


def is_instance_healthy(chal_id: str) -> bool:
    return is_runtime_instance_healthy(chal_id, DEFAULT_NAMESPACE)


def _cleanup_runtime_instance_impl(target_id: str, namespace: str | None) -> None:
    normalized_namespace = normalize_namespace(namespace)
    project_name = build_project_name(target_id, normalized_namespace)
    shared_network_name = build_docker_network_name(normalized_namespace)
    runtime_key = build_runtime_key(target_id, normalized_namespace)

    logger.info("Cleaning up project: %s ...", project_name)

    try:
        containers = client.containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={project_name}"},
        )
        for container in containers:
            try:
                if container.status == "running":
                    container.stop(timeout=5)
                container.remove(force=True, v=True)
                logger.info("Removed container: %s", container.name)
            except Exception as exc:
                logger.warning("Failed to remove container %s: %s", container.name, exc)
    except Exception as exc:
        logger.error("Error listing/removing containers for %s: %s", project_name, exc)

    try:
        networks = client.networks.list(
            filters={"label": f"com.docker.compose.project={project_name}"}
        )
        for network in networks:
            if network.name == shared_network_name:
                continue
            try:
                network.remove()
                logger.info("Removed network: %s", network.name)
            except Exception as exc:
                logger.warning("Failed to remove network %s: %s", network.name, exc)
    except Exception as exc:
        logger.error("Error cleaning networks: %s", exc)

    instance = pop_runtime_instance(target_id, normalized_namespace)
    if instance is not None:
        compose_path = instance.get("compose_path")
        if compose_path:
            try:
                Path(compose_path).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to remove runtime compose %s: %s", compose_path, exc)
        logger.info("Runtime instance %s removed from memory.", runtime_key)


def cleanup_runtime_instance(target_id: str, namespace: str | None) -> None:
    normalized_namespace = normalize_namespace(namespace)
    runtime_key = build_runtime_key(target_id, normalized_namespace)
    with target_locks.get_lock(runtime_key):
        _cleanup_runtime_instance_impl(target_id, normalized_namespace)


def cleanup_instance(chal_id: str) -> None:
    cleanup_runtime_instance(chal_id, DEFAULT_NAMESPACE)


async def monitor_runtime_instances() -> None:
    logger.info("Health monitor started.")
    while True:
        try:
            await asyncio.sleep(60)
            current_runtime_keys = snapshot_runtime_instance_keys()
            if not current_runtime_keys:
                continue

            logger.info("[Monitor] Scanning %s runtime instances for health...", len(current_runtime_keys))

            for runtime_key in current_runtime_keys:
                target_id, namespace = split_runtime_key(runtime_key)
                if get_runtime_instance(target_id, namespace) is None:
                    continue

                if not is_runtime_instance_healthy(target_id, namespace):
                    logger.warning(
                        "[Monitor] Runtime %s is unhealthy. Initiating auto-restart...",
                        runtime_key,
                    )
                    try:
                        await asyncio.to_thread(
                            launch_target,
                            target_id=target_id,
                            namespace=namespace,
                            force_recreate=True,
                        )
                        logger.info("[Monitor] Runtime %s successfully restarted.", runtime_key)
                    except Exception as exc:
                        logger.error("[Monitor] Failed to auto-restart %s: %s", runtime_key, exc)
        except asyncio.CancelledError:
            logger.info("[Monitor] Task cancelled, stopping.")
            break
        except Exception as exc:
            logger.error("[Monitor] Unexpected error in monitor loop: %s", exc)
            await asyncio.sleep(5)


monitor_instances = monitor_runtime_instances


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(monitor_runtime_instances())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    logger.info("Shutting down server, cleaning up all runtime instances...")
    current_runtime_keys = snapshot_runtime_instance_keys()
    if not current_runtime_keys:
        return

    tasks = [
        asyncio.to_thread(cleanup_runtime_instance, *split_runtime_key(runtime_key))
        for runtime_key in current_runtime_keys
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    clear_runtime_instances()


@app.delete("/launch/{target_id}", response_model=StopTargetResponse)
def stop_target(
    target_id: str,
    namespace: str = Query(DEFAULT_NAMESPACE, description="Runtime namespace for the target instance."),
) -> StopTargetResponse:
    normalized_namespace = normalize_namespace(namespace)
    if get_runtime_instance(target_id, normalized_namespace) is None:
        cleanup_runtime_instance(target_id, normalized_namespace)
        return StopTargetResponse(
            status="stopped",
            target_id=target_id,
            namespace=normalized_namespace,
            message="Runtime instance was not in memory, but cleanup was attempted.",
        )

    cleanup_runtime_instance(target_id, normalized_namespace)
    return StopTargetResponse(
        status="stopped",
        target_id=target_id,
        namespace=normalized_namespace,
        message="Runtime instance stopped and removed.",
    )


def _reused_launch_response(
    target_id: str,
    namespace: str,
    instance: dict,
) -> LaunchTargetResponse:
    return LaunchTargetResponse(
        status="reused",
        target_id=target_id,
        namespace=namespace,
        project_name=instance["project_name"],
        services=instance["services"],
    )


def _launch_target_impl(
    target_id: str,
    namespace: str | None,
    force_recreate: bool,
) -> LaunchTargetResponse:
    normalized_namespace = normalize_namespace(namespace)
    runtime_key = build_runtime_key(target_id, normalized_namespace)
    existing_instance = get_runtime_instance(target_id, normalized_namespace)
    should_recreate = False
    reason = ""

    if existing_instance:
        if force_recreate:
            should_recreate = True
            reason = "force_recreate=True"
        elif not is_runtime_instance_healthy(target_id, normalized_namespace):
            should_recreate = True
            reason = "instance unhealthy (port(s) closed)"
        else:
            logger.info("Reusing healthy runtime instance for %s", runtime_key)
            return _reused_launch_response(target_id, normalized_namespace, existing_instance)

    if should_recreate:
        logger.info("Recreating %s because %s", runtime_key, reason)
        _cleanup_runtime_instance_impl(target_id, normalized_namespace)

    targets = load_all_targets()
    if target_id not in targets:
        raise HTTPException(status_code=404, detail="Target not found")

    metadata = targets[target_id]
    adapter = build_default_registry().get(metadata["adapter_kind"])
    launch_spec = adapter.build_launch_spec(metadata)
    working_directory = Path(launch_spec.working_directory)

    if launch_spec.mode == "static":
        return LaunchTargetResponse(
            status="static",
            target_id=target_id,
            namespace=normalized_namespace,
        )

    project_name = build_project_name(target_id, normalized_namespace)
    docker_network = ensure_docker_network(normalized_namespace)
    runtime_compose_filename = build_runtime_compose_filename(normalized_namespace)
    runtime_compose_path = working_directory / runtime_compose_filename

    saved_existing_instance = existing_instance if should_recreate else None
    runtime_plan = materialize_compose_runtime(
        spec=launch_spec,
        project_name=project_name,
        docker_network=docker_network,
        host_ip=SERVICE_HOST_IP,
        runtime_compose_path=runtime_compose_path,
        find_free_port_fn=find_free_port,
        existing_external_ports=(saved_existing_instance or {}).get("external_ports"),
    )
    public_service_names = runtime_plan.public_service_names
    final_services = [TargetServiceInfo(**item) for item in runtime_plan.services]

    logger.info("Launching %s (recreate=%s)...", project_name, should_recreate)
    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "1"
    env.update(strip_cve_identifier_env(launch_spec.runtime_patches.get("compose_env", {}) or {}))
    env.update(strip_cve_identifier_env(load_env_file_vars(launch_spec.runtime_patches.get("env_file"))))
    ensure_docker_cli_config_dir(env, normalized_namespace)
    command = [
        "docker",
        "compose",
        "-p",
        project_name,
        "-f",
        runtime_compose_filename,
        "up",
        "-d",
        "--force-recreate",
        "--build",
    ]
    result = subprocess.run(
        command,
        cwd=working_directory,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Up failed:\n%s", result.stderr)
        raise HTTPException(status_code=500, detail=f"Docker up failed: {result.stderr}")

    try:
        containers_err = wait_for_containers_running(
            project_name=project_name,
            required_services=public_service_names if public_service_names else None,
            timeout_s=INSTANCE_STARTUP_TIMEOUT_S,
            poll_interval_s=INSTANCE_STARTUP_POLL_INTERVAL_S,
        )
        if containers_err:
            raise RuntimeError(containers_err)

        ports_err = wait_for_ports_open(
            services=final_services,
            timeout_s=INSTANCE_STARTUP_TIMEOUT_S,
            poll_interval_s=INSTANCE_STARTUP_POLL_INTERVAL_S,
        )
        if ports_err:
            raise RuntimeError(ports_err)
    except Exception as exc:
        details = {
            "error": str(exc),
            "project_name": project_name,
            "public_services": public_service_names,
            "services": [pydantic_to_dict(service) for service in final_services],
            "containers": summarize_project_containers(project_name),
        }
        logger.error("[LaunchVerify] Runtime %s failed to become ready: %s", runtime_key, details)
        cleanup_runtime_instance(target_id, normalized_namespace)
        raise HTTPException(status_code=500, detail=details)

    set_runtime_instance(
        target_id,
        normalized_namespace,
        {
            "target_id": target_id,
            "namespace": normalized_namespace,
            "project_name": project_name,
            "compose_path": runtime_compose_path,
            "services": final_services,
            "public_services": public_service_names,
            "external_ports": runtime_plan.external_ports,
        },
    )

    status = "recreated" if should_recreate else "launched"
    return LaunchTargetResponse(
        status=status,
        target_id=target_id,
        namespace=normalized_namespace,
        project_name=project_name,
        services=final_services,
    )


def _launch_challenge_impl(chal_id: str, force_recreate: bool) -> LaunchTargetResponse:
    return _launch_target_impl(chal_id, DEFAULT_NAMESPACE, force_recreate)


@app.get("/launch/{target_id}", response_model=LaunchTargetResponse)
def launch_target(
    target_id: str,
    namespace: str = Query(DEFAULT_NAMESPACE, description="Runtime namespace for the target instance."),
    force_recreate: bool = Query(
        False,
        description="If true, shut down the existing runtime instance and create a fresh one.",
    ),
) -> LaunchTargetResponse:
    normalized_namespace = normalize_namespace(namespace)
    runtime_key = build_runtime_key(target_id, normalized_namespace)
    lock = target_locks.get_lock(runtime_key)

    if force_recreate:
        def is_healthy() -> bool:
            instance = get_runtime_instance(target_id, normalized_namespace)
            return instance is not None and is_runtime_instance_healthy(target_id, normalized_namespace)

        def recover_action() -> LaunchTargetResponse:
            with lock:
                return _launch_target_impl(
                    target_id=target_id,
                    namespace=normalized_namespace,
                    force_recreate=True,
                )

        result = target_recovery_coordinator.run_serialized_recovery(
            runtime_key,
            is_healthy,
            recover_action,
        )
        if result == "reused_recent":
            existing_instance = get_runtime_instance(target_id, normalized_namespace)
            if existing_instance and is_runtime_instance_healthy(target_id, normalized_namespace):
                logger.info("Reusing recently recovered runtime instance for %s", runtime_key)
                return _reused_launch_response(target_id, normalized_namespace, existing_instance)
            with lock:
                return _launch_target_impl(
                    target_id=target_id,
                    namespace=normalized_namespace,
                    force_recreate=True,
                )
        return result

    with lock:
        return _launch_target_impl(
            target_id=target_id,
            namespace=normalized_namespace,
            force_recreate=force_recreate,
        )


launch_challenge = launch_target
stop_challenge = stop_target


def main() -> None:
    # Default to loopback so the server is not exposed to other hosts unless
    # the operator explicitly opts in by passing 0.0.0.0 (or another address).
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
