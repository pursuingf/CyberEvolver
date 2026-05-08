#!/usr/bin/env python3
"""FastAPI entry point for the challenge server.

Routes, lifecycle handlers, and the background health monitor live here.
Lower-level functionality is split across:
  - server_state: env config, docker client, port pool, instance registry, challenge cache
  - schemas: Pydantic request/response models
  - network_admin: docker network/container helpers
  - health_probes: container/port/HTTP readiness checks
  - launch_workflow: launch and cleanup orchestration
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Annotated, Optional

import uvicorn
from fastapi import FastAPI, Query

try:
    from bench_hub.server.path_bootstrap import ensure_repo_root_on_sys_path
except ModuleNotFoundError:
    from path_bootstrap import ensure_repo_root_on_sys_path

ensure_repo_root_on_sys_path(__file__)

# --- Re-exports for backward compatibility ---
# Tests and external callers patch / import these symbols from challenge_server.
# Keeping them here avoids breaking external mock.patch.object(challenge_server, "X") use.
from bench_hub.server.health_probes import (  # noqa: F401
    is_instance_healthy,
    is_port_open,
    probe_inner_service,
    wait_for_containers_running,
    wait_for_http_ready,
    wait_for_inner_services_ready,
    wait_for_ports_open,
    wait_for_services_healthy,
)
from bench_hub.server.launch_workflow import (  # noqa: F401
    _cleanup_instance_impl,
    _launch_challenge_impl,
    _reused_launch_response,
    build_network_debug,
    cleanup_instance,
    ensure_docker_cli_config_dir,
    load_env_file_vars,
    parse_internal_port,
    pydantic_to_dict,
    resolve_parallel_mode,
    resolve_server_target_scope,
)
from bench_hub.server.network_admin import (  # noqa: F401
    _has_active_endpoints_error,
    cleanup_orphan_networks,
    ensure_docker_network,
    list_project_containers,
    remove_network_with_retry,
    remove_own_docker_network as _remove_own_docker_network,
    resolve_service_inner_ips,
    summarize_project_containers,
)
from bench_hub.server.schemas import LaunchResponse, ServiceInfo, StopResponse  # noqa: F401
from bench_hub.server.server_state import (  # noqa: F401
    BASE_DIR,
    BENCHMARK_ROOT,
    COMPOSE_UP_TIMEOUT_S,
    CTF_NAMESPACE,
    DOCKER_NETWORK,
    HEALTH_POLL_INTERVAL_S,
    HEALTH_TIMEOUT_S,
    HOST_IP,
    INSTANCE_HEALTH_TIMEOUT_S,
    NETWORK_REMOVE_RETRY_INTERVAL_S,
    NETWORK_REMOVE_RETRY_TIMEOUT_S,
    PORT_OPEN_STABILITY_CHECKS,
    STARTUP_POLL_INTERVAL_S,
    STARTUP_TIMEOUT_S,
    challenge_locks,
    find_free_port,
    get_docker_client,
    get_running_instance,
    invalidate_challenge_cache,
    load_all_challenges,
    load_benchmark_sources,
    pop_running_instance,
    recovery_coordinator,
    release_allocated_port,
    running_instances,
    running_instances_lock,
    set_running_instance,
    snapshot_running_instance_ids,
    update_running_instance,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="CTF Manager Server")


async def monitor_instances():
    """Background task: every minute, check every running instance and auto-restart unhealthy ones."""
    logger.info("Health monitor started.")
    while True:
        try:
            await asyncio.sleep(60)
            current_ids = snapshot_running_instance_ids()
            if not current_ids:
                continue

            logger.info(f"[Monitor] Scanning {len(current_ids)} instances for health...")

            for instance_key in current_ids:
                info = get_running_instance(instance_key)
                if info is None:
                    continue
                lifecycle_state = str(info.get("lifecycle_state", "") or "").strip().lower()
                if lifecycle_state in {"stopping", "cleanup", "restarting"}:
                    logger.info(
                        "[Monitor] Skipping instance %s because lifecycle_state=%s",
                        instance_key,
                        lifecycle_state,
                    )
                    continue
                # per_agent instances are owned by individual workers; skip auto-restart.
                target_scope = str(info.get("target_scope", "") or "").strip().lower()
                if target_scope == "per_agent":
                    continue
                chal_id = str(info.get("chal_id") or instance_key)

                if not is_instance_healthy(instance_key):
                    logger.warning(
                        f"[Monitor] 🚨 Instance {instance_key} (challenge {chal_id}) found UNHEALTHY. Initiating auto-restart..."
                    )
                    try:
                        update_running_instance(instance_key, lifecycle_state="restarting")
                        if instance_key != chal_id:
                            await asyncio.to_thread(cleanup_instance, chal_id, instance_key)
                            await asyncio.to_thread(
                                launch_challenge,
                                chal_id=chal_id,
                                force_recreate=False,
                            )
                        else:
                            await asyncio.to_thread(
                                launch_challenge,
                                chal_id=chal_id,
                                force_recreate=True,
                            )
                        logger.info(f"[Monitor] ✅ Instance {instance_key} successfully restarted.")
                    except Exception as e:
                        logger.error(f"[Monitor] ❌ Failed to auto-restart {instance_key}: {e}")

        except asyncio.CancelledError:
            logger.info("[Monitor] Task cancelled, stopping.")
            break
        except Exception as e:
            logger.error(f"[Monitor] Unexpected error in monitor loop: {e}")
            await asyncio.sleep(5)


@app.on_event("startup")
async def startup_event():
    await asyncio.to_thread(cleanup_orphan_networks)
    ensure_docker_network()
    asyncio.create_task(monitor_instances())


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down server, cleaning up all containers...")
    chal_ids = snapshot_running_instance_ids()
    if chal_ids:
        tasks = [asyncio.to_thread(cleanup_instance, cid) for cid in chal_ids]
        await asyncio.gather(*tasks, return_exceptions=True)
        with running_instances_lock:
            running_instances.clear()

    await asyncio.to_thread(_remove_own_docker_network)


@app.delete("/launch/{chal_id}", response_model=StopResponse)
def stop_challenge(
    chal_id: str,
    run_id: Annotated[Optional[str], Query(description="Specific runtime instance id to stop.")] = None,
):
    """Stop and clean up a running challenge target."""
    lookup_key = run_id or chal_id
    if get_running_instance(lookup_key) is None:
        # Defensive cleanup: even when nothing is registered, force-remove any leftover containers.
        cleanup_instance(chal_id, run_id=run_id)
        return StopResponse(status="stopped", chal_id=chal_id, message="Instance was not in memory, but cleanup attempted.")

    update_running_instance(lookup_key, lifecycle_state="stopping")
    cleanup_instance(chal_id, run_id=run_id)
    return StopResponse(status="stopped", chal_id=chal_id, message="Instance stopped and removed.")


@app.get("/launch/{chal_id}", response_model=LaunchResponse)
def launch_challenge(
    chal_id: str,
    force_recreate: Annotated[bool, Query(description="If true, shutdown existing instance and create a fresh one.")] = False,
    parallel_mode: Annotated[Optional[str], Query(description="Parallelization strategy: network or alias.")] = None,
    target_scope: Annotated[Optional[str], Query(description="Target allocation scope: per_challenge or per_agent.")] = None,
):
    # Resolve target_scope early to decide locking strategy. For per_agent each launch
    # gets a unique run_id, so the per-chal lock would needlessly serialize concurrent
    # samples and cause multi-minute startup delays under batch concurrency.
    effective_scope = "per_challenge"
    try:
        challenges = load_all_challenges()
        if chal_id in challenges:
            effective_scope = resolve_server_target_scope(challenges[chal_id], target_scope)
    except Exception:
        pass

    if effective_scope == "per_agent":
        return _launch_challenge_impl(
            chal_id=chal_id,
            force_recreate=force_recreate,
            parallel_mode=parallel_mode,
            target_scope=target_scope,
        )

    lock = challenge_locks.get_lock(chal_id)

    if force_recreate:
        def is_healthy() -> bool:
            instance = get_running_instance(chal_id)
            return instance is not None and is_instance_healthy(chal_id)

        def recover_action() -> LaunchResponse:
            with lock:
                return _launch_challenge_impl(
                    chal_id=chal_id,
                    force_recreate=True,
                    parallel_mode=parallel_mode,
                    target_scope=target_scope,
                )

        result = recovery_coordinator.run_serialized_recovery(
            runtime_key=chal_id,
            is_healthy=is_healthy,
            recover_action=recover_action,
        )
        if result == "reused_recent":
            existing_instance = get_running_instance(chal_id)
            if existing_instance and is_instance_healthy(chal_id):
                logger.info("Reusing recently recovered instance for %s", chal_id)
                return _reused_launch_response(chal_id, existing_instance)
            with lock:
                return _launch_challenge_impl(
                    chal_id=chal_id,
                    force_recreate=True,
                    parallel_mode=parallel_mode,
                    target_scope=target_scope,
                )
        return result

    with lock:
        return _launch_challenge_impl(
            chal_id=chal_id,
            force_recreate=force_recreate,
            parallel_mode=parallel_mode,
            target_scope=target_scope,
        )


if __name__ == "__main__":
    # Default to loopback so the server is not exposed to other hosts unless
    # the operator explicitly opts in by passing 0.0.0.0 (or another address).
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    uvicorn.run(app, host=host, port=port)
