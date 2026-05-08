from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import os
from pathlib import Path
import threading
from typing import Any, Callable
import re

import yaml

from bench_hub.adapters.base import LaunchSpec


@dataclass
class ComposeRuntimePlan:
    compose_path: Path
    config: dict[str, Any]
    services: list[dict[str, Any]]
    public_service_names: list[str]
    external_ports: dict[str, int]
    agent_network_name: str | None = None


_PROJECT_LOCAL_SUBNET_LOCK = threading.Lock()
_PROJECT_LOCAL_RESERVED_SUBNETS: set[str] = set()


def parse_internal_port(port_def: Any) -> int | None:
    try:
        if isinstance(port_def, int):
            return port_def
        if isinstance(port_def, str):
            return int(port_def.split(":")[-1].split("/")[0])
    except (TypeError, ValueError):
        return None
    return None


def parse_port_protocol(port_def: Any) -> str | None:
    if not isinstance(port_def, str):
        return None
    text = port_def.strip().lower()
    if "/" not in text:
        return None
    protocol = text.rsplit("/", 1)[-1]
    if protocol in {"tcp", "udp"}:
        return protocol
    return None


def build_service_alias(project_name: str, service_name: str) -> str:
    return f"{project_name}_{service_name}"


def build_service_inner_host(project_name: str, service_name: str) -> str:
    token = re.sub(r"^ctf_[^_]+_", "", project_name)
    if token.endswith("_runtime"):
        token = token[: -len("_runtime")]
    if not token or token == "runtime":
        return service_name
    return f"{token}_{service_name}"


def materialize_compose_runtime(
    *,
    spec: LaunchSpec,
    project_name: str,
    docker_network: str,
    host_ip: str,
    runtime_compose_path: Path,
    find_free_port_fn: Callable[[], int],
    existing_external_ports: dict[str, int] | None,
    allocate_subnet_fn: Callable[[str, str, set[str]], str] | None = None,
) -> ComposeRuntimePlan:
    if spec.mode != "compose":
        raise ValueError(f"Cannot materialize non-compose launch spec: {spec.mode}")

    compose_env = dict(spec.runtime_patches.get("compose_env", {}) or {})
    config = _load_compose_stack(spec.compose_files, compose_env=compose_env)
    config = _expand_compose_env_values(config, compose_env)
    project_directory = _compose_project_directory(spec)
    _absolutize_compose_paths(config, project_directory, compose_env=compose_env)
    services_config = config.get("services", {}) or {}
    target_service_names = list(spec.target_services)
    launch_service_names = set(target_service_names) | set(spec.dependency_services)
    public_service_names: list[str] = []
    services: list[dict[str, Any]] = []
    external_ports: dict[str, int] = {}
    existing_external_ports = existing_external_ports or {}
    network_mode = str(spec.runtime_patches.get("network_mode", "") or "").lower()
    use_compose_project_local_networks = network_mode == "compose_project_local"
    parallel_mode = str(
        spec.runtime_patches.get("parallel_mode", "")
        or ("network" if use_compose_project_local_networks else "alias")
    ).strip().lower()
    if parallel_mode not in {"network", "alias"}:
        parallel_mode = "network" if use_compose_project_local_networks else "alias"
    agent_network = str(spec.runtime_patches.get("agent_network", "") or "").strip() or None
    agent_network_name = docker_network
    if use_compose_project_local_networks and agent_network:
        agent_network_name = f"{project_name}_{agent_network}"
    subnet_policy = {
        "pool": str(spec.runtime_patches.get("project_local_subnet_pool", "") or "").strip() or None,
        "prefix": spec.runtime_patches.get("project_local_subnet_prefix"),
    }

    filtered_services: dict[str, Any] = {}

    for service_name, service_config in services_config.items():
        if launch_service_names and service_name not in launch_service_names:
            continue

        service_config.pop("container_name", None)
        if use_compose_project_local_networks and parallel_mode == "network":
            service_alias = service_name
        else:
            service_alias = build_service_alias(project_name, service_name)
        if use_compose_project_local_networks:
            service_inner_host = service_name
        else:
            service_inner_host = build_service_inner_host(project_name, service_name)
            _inject_external_network(
                service_config,
                docker_network,
                aliases=[service_inner_host, service_alias],
                project_directory=project_directory,
                compose_env=compose_env,
            )

        if service_name not in target_service_names:
            service_config.pop("ports", None)
            filtered_services[service_name] = service_config
            services.append(
                {
                    "service_name": service_name,
                    "alias": service_alias,
                    "ip": host_ip,
                    "host": host_ip,
                    "port": None,
                    "inner_host": service_inner_host,
                    "inner_ip": None,
                    "inner_port": None,
                    "internal_port": None,
                    "external_host": host_ip,
                    "external_port": None,
                }
            )
            continue

        internal_port = None
        external_port = None
        protocol = "tcp"
        inferred_ports = (spec.runtime_patches.get("target_ports", {}) or {})
        inferred_protocols = (spec.runtime_patches.get("target_port_protocols", {}) or {})
        if service_name in inferred_ports:
            internal_port = inferred_ports.get(service_name)
            protocol = str(inferred_protocols.get(service_name, "tcp") or "tcp").lower()
        else:
            original_ports = service_config.get("ports", []) or []
            if original_ports:
                internal_port = parse_internal_port(original_ports[0])
                protocol = parse_port_protocol(original_ports[0]) or "tcp"

        if spec.exposure_mode == "host_ports" and internal_port is not None:
            external_port = existing_external_ports.get(service_name) or find_free_port_fn()
            port_suffix = f"/{protocol}" if protocol != "tcp" else ""
            service_config["ports"] = [f"{external_port}:{internal_port}{port_suffix}"]
            external_ports[service_name] = external_port
            public_service_names.append(service_name)
        else:
            service_config.pop("ports", None)

        filtered_services[service_name] = service_config

        services.append(
            {
                "service_name": service_name,
                "alias": service_alias,
                "ip": host_ip,
                "host": host_ip,
                "port": external_port,
                "protocol": protocol,
                "inner_host": service_inner_host,
                "inner_ip": None,
                "inner_port": internal_port,
                "internal_port": internal_port,
                "external_host": host_ip,
                "external_port": external_port,
            }
        )

    config["services"] = filtered_services
    _remap_conflicting_local_networks(
        config,
        project_name=project_name,
    )
    if use_compose_project_local_networks and parallel_mode == "network":
        _inject_compose_project_local_ipam(
            config,
            project_name=project_name,
            allocate_subnet_fn=allocate_subnet_fn
            or (
                lambda project_name, network_name, allocated_subnets: _allocate_project_local_subnet(
                    project_name,
                    network_name,
                    allocated_subnets,
                    pool_cidr=subnet_policy["pool"],
                    prefix=subnet_policy["prefix"],
                )
            ),
        )
    elif not use_compose_project_local_networks:
        config.setdefault("networks", {})
        config["networks"][docker_network] = {"external": True}

    runtime_compose_path.write_text(
        yaml.dump(config, default_flow_style=False, indent=2),
        encoding="utf-8",
    )

    return ComposeRuntimePlan(
        compose_path=runtime_compose_path,
        config=config,
        services=services,
        public_service_names=public_service_names,
        external_ports=external_ports,
        agent_network_name=agent_network_name,
    )


def _remap_conflicting_local_networks(
    config: dict[str, Any],
    *,
    project_name: str,
) -> None:
    networks_config = config.get("networks", {}) or {}
    if not isinstance(networks_config, dict):
        return

    remapped_networks: dict[str, tuple[ipaddress._BaseNetwork, ipaddress._BaseNetwork]] = {}
    allocated_subnets: set[str] = set()

    for network_name, raw_network_config in list(networks_config.items()):
        network_config = raw_network_config
        if network_config is None:
            network_config = {}
        if not isinstance(network_config, dict):
            continue
        if network_config.get("external"):
            continue

        existing_ipam = dict(network_config.get("ipam", {}) or {})
        existing_configs = list(existing_ipam.get("config", []) or [])
        if not existing_configs:
            continue

        subnet = str((existing_configs[0] or {}).get("subnet", "") or "").strip()
        if not subnet:
            continue

        try:
            original_network = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            continue

        conflicting_subnets = _collect_used_docker_subnets(original_network)
        if not any(original_network.overlaps(used) for used in conflicting_subnets):
            continue

        remapped_subnet = _allocate_remapped_runtime_subnet(
            project_name,
            network_name,
            allocated_subnets,
            str(original_network),
        )
        allocated_subnets.add(remapped_subnet)
        remapped_network = ipaddress.ip_network(remapped_subnet, strict=False)

        updated_ipam = dict(existing_ipam)
        updated_configs = list(existing_configs)
        updated_entry = dict(updated_configs[0] or {})
        updated_entry["subnet"] = remapped_subnet
        updated_configs[0] = updated_entry
        updated_ipam["config"] = updated_configs
        network_config["ipam"] = updated_ipam
        networks_config[network_name] = network_config
        remapped_networks[network_name] = (original_network, remapped_network)

    if not remapped_networks:
        return

    services_config = config.get("services", {}) or {}
    for service_config in services_config.values():
        service_networks = service_config.get("networks", {}) or {}
        if not isinstance(service_networks, dict):
            continue

        for network_name, network_membership in service_networks.items():
            mapping = remapped_networks.get(network_name)
            if not mapping or not isinstance(network_membership, dict):
                continue
            original_network, remapped_network = mapping
            raw_ip = str(network_membership.get("ipv4_address", "") or "").strip()
            if not raw_ip:
                continue
            try:
                original_ip = ipaddress.ip_address(raw_ip)
            except ValueError:
                continue
            if original_ip not in original_network:
                continue

            offset = int(original_ip) - int(original_network.network_address)
            remapped_ip = ipaddress.ip_address(int(remapped_network.network_address) + offset)
            network_membership["ipv4_address"] = str(remapped_ip)

    config["networks"] = networks_config


def _inject_compose_project_local_ipam(
    config: dict[str, Any],
    *,
    project_name: str,
    allocate_subnet_fn: Callable[[str, str, set[str]], str],
) -> None:
    networks_config = config.get("networks", {}) or {}
    allocated_subnets: set[str] = set()

    for network_name, raw_network_config in list(networks_config.items()):
        network_config = raw_network_config
        if network_config is None:
            network_config = {}
        if not isinstance(network_config, dict):
            continue
        if network_config.get("external"):
            networks_config[network_name] = network_config
            continue

        existing_ipam = dict(network_config.get("ipam", {}) or {})
        existing_configs = list(existing_ipam.get("config", []) or [])
        if existing_configs:
            subnet = str((existing_configs[0] or {}).get("subnet", "") or "").strip()
            if subnet:
                allocated_subnets.add(subnet)
            networks_config[network_name] = network_config
            continue

        subnet = allocate_subnet_fn(project_name, network_name, allocated_subnets)
        allocated_subnets.add(subnet)
        network_config["ipam"] = {"config": [{"subnet": subnet}]}
        networks_config[network_name] = network_config

    config["networks"] = networks_config


def _allocate_remapped_runtime_subnet(
    project_name: str,
    network_name: str,
    allocated_subnets: set[str],
    original_subnet: str,
) -> str:
    original_network = ipaddress.ip_network(original_subnet, strict=False)
    pool_cidr = os.getenv("CTF_RUNTIME_REMAP_SUBNET_POOL", "172.16.0.0/12").strip() or "172.16.0.0/12"
    pool = ipaddress.ip_network(pool_cidr, strict=False)
    prefix = original_network.prefixlen

    if prefix < pool.prefixlen:
        raise RuntimeError(
            f"Unable to remap {original_subnet}: pool {pool_cidr} is smaller than requested /{prefix}"
        )

    candidates = [pool] if prefix == pool.prefixlen else list(pool.subnets(new_prefix=prefix))
    if not candidates:
        raise RuntimeError(f"No candidate subnets available in {pool_cidr} with prefix /{prefix}")

    used_networks = _collect_used_docker_subnets(pool)
    used_networks.extend(ipaddress.ip_network(subnet, strict=False) for subnet in allocated_subnets)

    seed = f"{project_name}:{network_name}:{original_subnet}".encode("utf-8")
    start_index = int(hashlib.sha1(seed).hexdigest(), 16) % len(candidates)

    for offset in range(len(candidates)):
        candidate = candidates[(start_index + offset) % len(candidates)]
        if any(candidate.overlaps(used_network) for used_network in used_networks):
            continue
        return str(candidate)

    raise RuntimeError(f"Unable to allocate remapped subnet for {original_subnet} from {pool_cidr}")


def _allocate_project_local_subnet(
    project_name: str,
    network_name: str,
    allocated_subnets: set[str],
    *,
    pool_cidr: str | None = None,
    prefix: int | str | None = None,
) -> str:
    resolved_pool = str(
        pool_cidr
        or os.getenv("CTF_PROJECT_LOCAL_SUBNET_POOL", "172.31.0.0/16").strip()
        or "172.31.0.0/16"
    )
    resolved_prefix = int(
        prefix
        if prefix is not None
        else os.getenv("CTF_PROJECT_LOCAL_SUBNET_PREFIX", "28")
    )
    pool = ipaddress.ip_network(resolved_pool, strict=False)
    if resolved_prefix < pool.prefixlen:
        raise RuntimeError(
            f"Unable to allocate /{resolved_prefix} from pool {resolved_pool}: prefix is broader than pool"
        )
    candidates = list(pool.subnets(new_prefix=resolved_prefix))
    if not candidates:
        raise RuntimeError(f"No candidate subnets available in {resolved_pool} with prefix /{resolved_prefix}")

    seed = f"{project_name}:{network_name}".encode("utf-8")
    start_index = int(hashlib.sha1(seed).hexdigest(), 16) % len(candidates)

    with _PROJECT_LOCAL_SUBNET_LOCK:
        used_networks = _collect_used_docker_subnets(pool)
        used_networks.extend(
            ipaddress.ip_network(subnet, strict=False)
            for subnet in allocated_subnets
        )
        used_networks.extend(
            ipaddress.ip_network(subnet, strict=False)
            for subnet in _PROJECT_LOCAL_RESERVED_SUBNETS
        )

        for offset in range(len(candidates)):
            candidate = candidates[(start_index + offset) % len(candidates)]
            if any(candidate.overlaps(used_network) for used_network in used_networks):
                continue
            subnet = str(candidate)
            _PROJECT_LOCAL_RESERVED_SUBNETS.add(subnet)
            return subnet

    raise RuntimeError(f"Unable to allocate an available /{resolved_prefix} subnet from {resolved_pool}")


def _release_reserved_project_local_subnets(pool_cidr: str | None = None) -> None:
    with _PROJECT_LOCAL_SUBNET_LOCK:
        if pool_cidr is None:
            _PROJECT_LOCAL_RESERVED_SUBNETS.clear()
            return
        pool = ipaddress.ip_network(pool_cidr, strict=False)
        stale = [
            subnet
            for subnet in _PROJECT_LOCAL_RESERVED_SUBNETS
            if ipaddress.ip_network(subnet, strict=False).overlaps(pool)
        ]
        for subnet in stale:
            _PROJECT_LOCAL_RESERVED_SUBNETS.discard(subnet)


def release_reserved_project_local_subnet(subnet_cidr: str | None) -> None:
    if not subnet_cidr:
        return
    subnet = str(ipaddress.ip_network(subnet_cidr, strict=False))
    with _PROJECT_LOCAL_SUBNET_LOCK:
        _PROJECT_LOCAL_RESERVED_SUBNETS.discard(subnet)


def _collect_used_docker_subnets(pool: ipaddress._BaseNetwork) -> list[ipaddress._BaseNetwork]:
    try:
        import docker
    except Exception:
        return []

    try:
        client = docker.from_env()
        networks = client.networks.list()
    except Exception:
        return []

    used: list[ipaddress._BaseNetwork] = []
    for network in networks:
        attrs = getattr(network, "attrs", {}) or {}
        ipam_configs = ((attrs.get("IPAM", {}) or {}).get("Config") or [])
        for config in ipam_configs:
            subnet = str((config or {}).get("Subnet", "") or "").strip()
            if not subnet:
                continue
            try:
                parsed = ipaddress.ip_network(subnet, strict=False)
            except ValueError:
                continue
            if parsed.version != pool.version:
                continue
            if parsed.overlaps(pool):
                used.append(parsed)
    return used


def _load_compose_stack(compose_files: list[str], compose_env: dict[str, str] | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    compose_env = compose_env or {}
    for compose_file in compose_files:
        current = _load_compose_file(Path(compose_file), compose_env=compose_env, seen_paths=set())
        merged = _merge_compose_dicts(merged, current)
    return merged


def _merge_compose_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_compose_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _inject_external_network(
    service_config: dict[str, Any],
    docker_network: str,
    *,
    aliases: list[str] | None,
    project_directory: Path,
    compose_env: dict[str, str],
) -> None:
    if _service_uses_network_mode(
        service_config,
        project_directory=project_directory,
        compose_env=compose_env,
    ):
        return
    networks = service_config.setdefault("networks", {})
    if isinstance(networks, list):
        networks = {name: {} for name in networks}
        service_config["networks"] = networks
    network_config = networks.get(docker_network)
    if not isinstance(network_config, dict):
        network_config = {}
        networks[docker_network] = network_config

    desired_aliases = [alias for alias in (aliases or []) if alias]
    if desired_aliases:
        existing_aliases = list(network_config.get("aliases", []) or [])
        for alias in desired_aliases:
            if alias not in existing_aliases:
                existing_aliases.append(alias)
        network_config["aliases"] = existing_aliases


def _compose_project_directory(spec: LaunchSpec) -> Path:
    if spec.compose_files:
        return Path(spec.compose_files[0]).resolve().parent
    return Path(spec.working_directory).resolve()


def _absolutize_compose_paths(config: dict[str, Any], project_directory: Path, compose_env: dict[str, str] | None = None) -> None:
    compose_env = compose_env or {}
    services_config = config.get("services", {}) or {}
    for service_config in services_config.values():
        extends = service_config.get("extends")
        if isinstance(extends, dict):
            extends_file = extends.get("file")
            if isinstance(extends_file, str):
                extends["file"] = _resolve_compose_path(extends_file, project_directory, compose_env)

        build = service_config.get("build")
        if isinstance(build, str):
            service_config["build"] = _resolve_compose_path(build, project_directory, compose_env)
        elif isinstance(build, dict):
            context = build.get("context")
            if isinstance(context, str):
                build["context"] = _resolve_compose_path(context, project_directory, compose_env)
            dockerfile = build.get("dockerfile")
            if isinstance(dockerfile, str) and not Path(dockerfile).is_absolute():
                context_root = Path(build.get("context", project_directory))
                build["dockerfile"] = str((context_root / dockerfile).resolve())

        volumes = service_config.get("volumes", []) or []
        service_config["volumes"] = [_absolutize_volume(volume, project_directory, compose_env) for volume in volumes]


def _resolve_compose_path(raw_path: str, project_directory: Path, compose_env: dict[str, str] | None = None) -> str:
    expanded_path = _expand_compose_env(raw_path, compose_env or {})
    path = Path(expanded_path).expanduser()
    if path.is_absolute():
        return str(path)
    return str((project_directory / path).resolve())


def _absolutize_volume(volume: Any, project_directory: Path, compose_env: dict[str, str]) -> Any:
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) < 2:
            return volume
        source = parts[0]
        if _looks_like_bind_source(source):
            parts[0] = _resolve_compose_path(source, project_directory, compose_env)
            return ":".join(parts)
        return volume

    if isinstance(volume, dict):
        source = volume.get("source")
        volume_type = volume.get("type")
        if isinstance(source, str) and (volume_type == "bind" or _looks_like_bind_source(source)):
            volume = dict(volume)
            volume["source"] = _resolve_compose_path(source, project_directory, compose_env)
        return volume

    return volume


def _looks_like_bind_source(source: str) -> bool:
    return source.startswith(".") or source.startswith("~") or "/" in source


def _service_uses_network_mode(
    service_config: dict[str, Any],
    *,
    project_directory: Path,
    compose_env: dict[str, str],
) -> bool:
    if service_config.get("network_mode"):
        return True

    extends = service_config.get("extends")
    if not isinstance(extends, dict):
        return False

    ext_file = extends.get("file")
    ext_service = extends.get("service")
    if not isinstance(ext_file, str) or not isinstance(ext_service, str):
        return False

    ext_path = Path(_resolve_compose_path(ext_file, project_directory, compose_env))
    if not ext_path.exists():
        return False

    ext_config = _load_compose_file(ext_path, compose_env, seen_paths=set())
    ext_service_config = ((ext_config.get("services", {}) or {}).get(ext_service, {}) or {})
    return _service_uses_network_mode(
        ext_service_config,
        project_directory=ext_path.parent,
        compose_env=compose_env,
    )


def _expand_compose_env(value: str, compose_env: dict[str, str]) -> str:
    pieces: list[str] = []
    cursor = 0
    while cursor < len(value):
        start = value.find("${", cursor)
        if start == -1:
            pieces.append(value[cursor:])
            break
        if start > cursor:
            pieces.append(value[cursor:start])
        expanded, cursor = _expand_compose_expr(value, start, compose_env)
        pieces.append(expanded)
    return "".join(pieces)


def _expand_compose_expr(value: str, start: int, compose_env: dict[str, str]) -> tuple[str, int]:
    cursor = start + 2
    depth = 1
    while cursor < len(value) and depth > 0:
        if value.startswith("${", cursor):
            depth += 1
            cursor += 2
            continue
        if value[cursor] == "}":
            depth -= 1
        cursor += 1

    expression = value[start + 2 : cursor - 1]
    if ":-" in expression:
        var_name, default_value = expression.split(":-", 1)
        var_name = var_name.strip()
        if compose_env.get(var_name):
            return compose_env[var_name], cursor
        return _expand_compose_env(default_value, compose_env), cursor
    if ":?" in expression:
        var_name, _error_message = expression.split(":?", 1)
        var_name = var_name.strip()
        if compose_env.get(var_name):
            return compose_env[var_name], cursor
        return f"${{{expression}}}", cursor

    var_name = expression.strip()
    return compose_env.get(var_name, f"${{{expression}}}"), cursor


def _expand_compose_env_values(value: Any, compose_env: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _expand_compose_env(value, compose_env)
    if isinstance(value, list):
        return [_expand_compose_env_values(item, compose_env) for item in value]
    if isinstance(value, dict):
        return {
            key: _expand_compose_env_values(item, compose_env)
            for key, item in value.items()
        }
    return value


def _load_compose_file(path: Path, compose_env: dict[str, str], seen_paths: set[Path]) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved in seen_paths:
        return {}
    seen_paths.add(resolved)

    with open(resolved, "r", encoding="utf-8") as handle:
        current = yaml.safe_load(handle) or {}

    merged: dict[str, Any] = {}
    include_entries = current.get("include", []) or []
    if isinstance(include_entries, (str, dict)):
        include_entries = [include_entries]

    for entry in include_entries:
        include_path = None
        if isinstance(entry, str):
            include_path = Path(_resolve_compose_path(entry, resolved.parent, compose_env))
        elif isinstance(entry, dict):
            raw_path = entry.get("path")
            if isinstance(raw_path, str):
                include_path = Path(_resolve_compose_path(raw_path, resolved.parent, compose_env))
        if include_path is not None:
            merged = _merge_compose_dicts(merged, _load_compose_file(include_path, compose_env, seen_paths))

    current = dict(current)
    current.pop("include", None)
    merged = _merge_compose_dicts(merged, current)
    return merged
