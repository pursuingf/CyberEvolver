import logging
import os
import socket
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from sshtunnel import SSHTunnelForwarder  # Used for SSH jump hosts.

from bench_hub.adapters.roots import resolve_configured_benchmark_root
from bench_hub.adapters.source_config import build_default_registry, resolve_benchmark_sources
from common.utils.runtime_policy import normalize_target_scope, resolve_target_scope


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a float, got {raw!r}") from exc


# ==========================================
# Configuration classes
# ==========================================

@dataclass
class SSHConfig:
    """Configuration for the SSH jump host."""

    jump_host: str = field(default_factory=lambda: _env_str("CHALLENGE_SSH_JUMP_HOST"))
    jump_port: int = field(default_factory=lambda: _env_int("CHALLENGE_SSH_JUMP_PORT", 22))
    jump_user: str = field(default_factory=lambda: _env_str("CHALLENGE_SSH_JUMP_USER"))
    ssh_key_path: str = field(default_factory=lambda: _env_str("CHALLENGE_SSH_KEY_PATH"))
    remote_bind_address: str = field(default_factory=lambda: _env_str("CHALLENGE_SSH_REMOTE_BIND_ADDRESS"))
    remote_bind_port: int = field(default_factory=lambda: _env_int("CHALLENGE_SSH_REMOTE_BIND_PORT", 8000))
    local_bind_host: str = field(default_factory=lambda: _env_str("CHALLENGE_SSH_LOCAL_BIND_HOST", "0.0.0.0"))
    local_bind_port: int = field(default_factory=lambda: _env_int("CHALLENGE_SSH_LOCAL_BIND_PORT", 0))
    control_access_host: str = field(default_factory=lambda: _env_str("CHALLENGE_SSH_CONTROL_ACCESS_HOST", "127.0.0.1"))


@dataclass
class ChallengeClientConfig:
    """Top-level environment configuration."""

    # === Basic configuration ===
    benchmark_root: str = "./bench_hub/benchmarks"
    benchmark_sources: list[dict[str, Any]] | None = None

    # === Mode selection: 'remote'; 'local' is intentionally not implemented ===
    run_mode: str = "remote"

    # === Remote mode configuration ===
    server_url: str = field(default_factory=lambda: _env_str("CHALLENGE_SERVER_URL"))
    use_ssh_tunnel: bool = False  # Route control and service traffic through the jump host.
    ssh_config: SSHConfig = field(default_factory=SSHConfig)

    # True: return IP:port for external access.
    # False: return alias:port for in-container network access.
    use_external_access: bool = True

    # IP used by the agent container to reach the host machine. Required when
    # use_ssh_tunnel=True and use_external_access=True.
    host_ip_for_agent: str = field(default_factory=lambda: _env_str("CHALLENGE_HOST_IP_FOR_AGENT"))

    # Fallback network name used only when the runtime API does not return one.
    network_name: str = field(default_factory=lambda: _env_str("CHALLENGE_NETWORK_NAME", "ctfnet"))

    request_timeout_s: float = field(default_factory=lambda: _env_float("CHALLENGE_REQUEST_TIMEOUT_S", 300.0))
    teardown_timeout_s: float = field(default_factory=lambda: _env_float("CHALLENGE_TEARDOWN_TIMEOUT_S", 10.0))
    connectivity_timeout_s: float = field(default_factory=lambda: _env_float("CHALLENGE_CONNECTIVITY_TIMEOUT_S", 1.0))

# ==========================================
# Backend interface
# ==========================================

class BackendStrategy(ABC):
    def __init__(self, config: ChallengeClientConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger

    @abstractmethod
    def initialize(
        self,
        challenge_id: str,
        metadata: dict,
        force_recreate: bool = False,
        runtime_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    def teardown(self, challenge_id: str):
        pass

    @abstractmethod
    def validate_connectivity(self, challenge_id: str, record: dict) -> bool:
        pass

    @abstractmethod
    def handle_crash(self, challenge_id: str, observation: str) -> tuple[str, bool]:
        pass

    def cleanup(self):
        """Clean up resources before exit."""
        return None

# ==========================================
# Remote backend (SSH + API)
# ==========================================

class RemoteBackend(BackendStrategy):
    def __init__(self, config: ChallengeClientConfig, logger: logging.Logger):
        super().__init__(config, logger)
        if not config.server_url:
            raise ValueError(
                "Challenge server URL is required. Set ChallengeClientConfig.server_url "
                "or the CHALLENGE_SERVER_URL environment variable."
            )
        self.tunnel: SSHTunnelForwarder | None = None
        self.api_base_url = config.server_url

        # SSH tunnel for control traffic: client -> server API.
        if config.use_ssh_tunnel:
            self._validate_ssh_config()
            self._start_control_tunnel()

        # Data tunnel pool: {chal_id: [tunnel_obj, ...]}.
        # Used for service access: client -> jump host -> target:port.
        self.service_tunnels = {}

    def _validate_ssh_config(self):
        ssh_cfg = self.config.ssh_config
        missing = [
            name
            for name, value in {
                "ssh_config.jump_host": ssh_cfg.jump_host,
                "ssh_config.jump_user": ssh_cfg.jump_user,
                "ssh_config.ssh_key_path": ssh_cfg.ssh_key_path,
                "ssh_config.remote_bind_address": ssh_cfg.remote_bind_address,
                "ssh_config.local_bind_host": ssh_cfg.local_bind_host,
                "ssh_config.control_access_host": ssh_cfg.control_access_host,
            }.items()
            if not value
        ]
        if self.config.use_external_access and not self.config.host_ip_for_agent:
            missing.append("host_ip_for_agent")
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"SSH tunnel mode requires explicit configuration for: {joined}")

    def _start_control_tunnel(self):
        ssh_cfg = self.config.ssh_config
        self.logger.info(f"[SSH] Opening control tunnel via {ssh_cfg.jump_user}@{ssh_cfg.jump_host}...")

        self.tunnel = SSHTunnelForwarder(
            (ssh_cfg.jump_host, ssh_cfg.jump_port),
            ssh_username=ssh_cfg.jump_user,
            ssh_pkey=ssh_cfg.ssh_key_path,
            remote_bind_address=(ssh_cfg.remote_bind_address, ssh_cfg.remote_bind_port),
            # Bind broadly so external clients such as Docker containers can connect,
            # even though the API is usually consumed only by the host process.
            local_bind_address=(ssh_cfg.local_bind_host, ssh_cfg.local_bind_port)
        )
        self.tunnel.start()
        # The manager process itself talks to the API through localhost.
        self.api_base_url = f"http://{ssh_cfg.control_access_host}:{self.tunnel.local_bind_port}"
        self.logger.info(f"[SSH] Tunnel established! API mapped to {self.api_base_url}")

    def initialize(
        self,
        challenge_id: str,
        metadata: dict,
        force_recreate: bool = False,
        runtime_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Implement the initialize backend operation."""
        url = f"{self.api_base_url}/launch/{challenge_id}"

        params: dict[str, str] = {}
        if force_recreate:
            params["force_recreate"] = "true"
        parallel_mode = str((runtime_args or {}).get("parallel_mode", "") or "").strip().lower()
        if parallel_mode:
            params["parallel_mode"] = parallel_mode
        target_scope = normalize_target_scope((runtime_args or {}).get("target_scope"))
        if target_scope:
            params["target_scope"] = target_scope
        if not params:
            params = None
        resp = requests.get(url, params=params, timeout=self.config.request_timeout_s)
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status")

        # Static challenge.
        if status == "static":
            return {
                "id": challenge_id,
                "type": "static",
                "files": metadata.get("files", []),
                "work_dir": str(metadata.get("full_path"))
            }

        # Dynamic challenge.
        if status in ["launched", "reused", "recreated"]:
            raw_services = data.get("services", [])
            processed_services = self._process_services(challenge_id, raw_services)

            return {
                "id": challenge_id,
                "type": "dynamic",
                "status": status,
                "run_id": data.get("run_id"),
                "project_name": data.get("project_name"),
                "network_name": data.get("network_name"),
                "network_subnet": data.get("network_subnet"),
                "network_gateway": data.get("network_gateway"),
                "scoring": data.get("scoring", {}),
                "debug": dict(data.get("debug", {}) or {}),
                "services": processed_services,
            }

        raise RuntimeError(f"Unknown status: {status}")

    def _process_services(self, chal_id: str, raw_services: list[dict]) -> dict:
        """
        Process service metadata from the runtime API.

        When SSH tunneling is enabled, this also creates a per-service SSH
        tunnel and binds it to 0.0.0.0 so agent containers can connect.
        """
        processed = {}

        # Clear any stale tunnels from a prior launch.
        self._clear_service_tunnels(chal_id)
        current_tunnels = []

        for svc in raw_services:
            svc_name = svc["service_name"]
            processed_service = dict(svc)
            inner_host = processed_service.get("inner_host") or processed_service.get("inner_ip") or processed_service.get("alias")
            inner_port = processed_service.get("inner_port")
            if inner_port is None:
                inner_port = processed_service.get("internal_port")
            external_host = processed_service.get("external_host") or processed_service.get("ip")
            external_port = processed_service.get("external_port")

            if self.config.use_external_access:
                # The server returns an externally reachable host:port pair.
                remote_host = external_host
                remote_port = external_port
                final_host = remote_host
                final_port = remote_port

                if remote_port:
                    # Create a data tunnel when SSH forwarding is enabled.
                    if self.config.use_ssh_tunnel:
                        try:
                            # Dynamic tunnel: host random port -> jump host -> target:port.
                            svc_tunnel = SSHTunnelForwarder(
                                (self.config.ssh_config.jump_host, self.config.ssh_config.jump_port),
                                ssh_username=self.config.ssh_config.jump_user,
                                ssh_pkey=self.config.ssh_config.ssh_key_path,
                                remote_bind_address=(remote_host, remote_port),
                                local_bind_address=(
                                    self.config.ssh_config.local_bind_host,
                                    self.config.ssh_config.local_bind_port,
                                ),
                            )
                            svc_tunnel.start()
                            current_tunnels.append(svc_tunnel)

                            final_host = self.config.host_ip_for_agent
                            final_port = svc_tunnel.local_bind_port

                            self.logger.info(f"[SSH] Forwarding Service {svc_name}: 0.0.0.0:{final_port} (Agent use {final_host}) -> {remote_host}:{remote_port}")
                        except Exception as e:
                            self.logger.error(f"Failed to tunnel service {svc_name}: {e}")
                    processed_service["external_host"] = final_host
                    processed_service["external_port"] = final_port
                processed_service["host"] = final_host
                processed_service["port"] = final_port
                if final_host and final_port is not None:
                    processed_service["url"] = f"http://{final_host}:{final_port}"
                    processed_service["netcat"] = f"nc {final_host} {final_port}"
                processed[svc_name] = processed_service
            else:
                # Internal mode returns the Docker/network alias.
                final_host = inner_host
                final_port = inner_port
                processed_service["host"] = final_host
                processed_service["port"] = final_port
                if final_host and final_port is not None:
                    processed_service["url"] = f"http://{final_host}:{final_port}"
                    processed_service["netcat"] = f"nc {final_host} {final_port}"
                processed[svc_name] = processed_service

        if current_tunnels:
            self.service_tunnels[chal_id] = current_tunnels

        return processed

    def teardown(self, challenge_id: str, run_id: str | None = None):
        """Implement the teardown backend operation."""
        # 1. Stop the service.
        try:
            params = {"run_id": run_id} if run_id else None
            requests.delete(
                f"{self.api_base_url}/launch/{challenge_id}",
                params=params,
                timeout=self.config.teardown_timeout_s,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass

        # 2. Clear data tunnels for this challenge.
        self._clear_service_tunnels(challenge_id)

    def _clear_service_tunnels(self, chal_id: str):
        if chal_id in self.service_tunnels:
            for t in self.service_tunnels[chal_id]:
                t.stop()
            del self.service_tunnels[chal_id]

    def validate_connectivity(self, challenge_id: str, record: dict) -> bool:
        """Implement the validate_connectivity backend operation."""
        if record["type"] == "static":
            return True
        if not self.config.use_external_access:
            return True

        for svc in record.get('services', {}).values():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.config.connectivity_timeout_s)
            try:
                # In SSH mode, final_host is the host IP exposed to the agent.
                # The host can usually also reach its own tunnel binding, but
                # svc["host"] should match the address the agent container uses.
                res = s.connect_ex((svc['host'], int(svc['port'])))
                if res != 0:
                    return False
            finally:
                s.close()
        return True

    def handle_crash(self, challenge_id: str, observation: str) -> tuple[str, bool]:
        """Implement the handle_crash backend operation."""
        # Remote forced-restart handling is delegated to the caller for now.
        # Return True to indicate that the observation was accepted as handled.
        return observation, True

    def cleanup(self):
        """Cleanup hook that may be called by the base manager."""
        if self.tunnel:
            self.tunnel.stop()
        for tunnels in self.service_tunnels.values():
            for t in tunnels:
                t.stop()

# ==========================================
# Manager class
# ==========================================

class ChallengeClient:
    def __init__(self, config: ChallengeClientConfig | None = None, logger = None):
        self.config = config or ChallengeClientConfig()
        self.logger = logger or logging.getLogger("ChallengeClient")
        self._validate_run_mode()

        if self.config.run_mode == "local":
            raise NotImplementedError("ChallengeClient run_mode='local' is not implemented.")
        if not self.config.server_url:
            raise ValueError(
                "Challenge server URL is required. Set ChallengeClientConfig.server_url "
                "or the CHALLENGE_SERVER_URL environment variable."
            )

        self.benchmark_root = resolve_configured_benchmark_root(self.config.benchmark_root)
        self.benchmark_registry = build_default_registry()
        self.benchmark_sources = resolve_benchmark_sources(self.config)

        # Load challenge metadata.
        self.challenges = self._load_metadata()

        # Runtime state caches.
        self._runtime_cache = {}
        self._runtime_args_cache = {}

        self.logger.info(f"Initializing REMOTE backend ({self.config.server_url})...")
        self.backend = RemoteBackend(self.config, self.logger)

    def _validate_run_mode(self) -> None:
        if self.config.run_mode not in {"local", "remote"}:
            raise ValueError(f"Unsupported run_mode: {self.config.run_mode!r}")

    def get_challenge_data(
        self,
        challenge_id: str,
        auto_init: bool = True,
        runtime_args: dict[str, Any] | None = None,
    ) -> dict:
        if challenge_id not in self.challenges:
            raise ValueError(f"Challenge {challenge_id} not found.")

        meta = self.challenges[challenge_id]
        resolved_runtime_args = self._resolve_runtime_args(challenge_id, runtime_args)

        # Reuse an existing runtime record when present.
        if challenge_id in self._runtime_cache:
            return self._apply_runtime_record(challenge_id, self._runtime_cache[challenge_id], meta)

        # Initialize the runtime when requested.
        if auto_init:
            try:
                if resolved_runtime_args:
                    record = self.backend.initialize(
                        challenge_id,
                        meta,
                        runtime_args=resolved_runtime_args,
                    )
                else:
                    record = self.backend.initialize(challenge_id, meta)
                return self._apply_runtime_record(challenge_id, record, meta)
            except Exception as e:
                meta["target_status"] = "stopped"
                meta["target_info"] = {}
                meta["runtime"] = {}
                self.logger.error(f"Init failed: {e}")

        self.challenges[challenge_id] |= meta
        return meta

    def refresh_challenge_data(
        self,
        challenge_id: str,
        force_recreate: bool = False,
        runtime_args: dict[str, Any] | None = None,
    ) -> dict:
        if challenge_id not in self.challenges:
            raise ValueError(f"Challenge {challenge_id} not found.")

        meta = self.challenges[challenge_id]
        resolved_runtime_args = self._resolve_runtime_args(challenge_id, runtime_args)
        target_scope = resolve_target_scope(chal_data=meta, runtime_args=resolved_runtime_args)
        try:
            if force_recreate and target_scope == "per_agent" and challenge_id in self._runtime_cache:
                self._backend_teardown_for_record(challenge_id)
                self._clear_runtime_state(challenge_id, drop_runtime_args=False)
                force_recreate = False
            if resolved_runtime_args:
                record = self.backend.initialize(
                    challenge_id,
                    meta,
                    force_recreate=force_recreate,
                    runtime_args=resolved_runtime_args,
                )
            else:
                record = self.backend.initialize(
                    challenge_id,
                    meta,
                    force_recreate=force_recreate,
                )
        except Exception as e:
            self._runtime_cache.pop(challenge_id, None)
            meta["target_status"] = "stopped"
            meta["target_info"] = {}
            meta["runtime"] = {}
            self.logger.error(f"Refresh failed for {challenge_id}: {e}")
            raise
        return self._apply_runtime_record(challenge_id, record, meta)

    def finish_challenge(self, challenge_id: str):
        """Release resources after a challenge is complete."""
        self.teardown(challenge_id)

    def teardown(self, challenge_id: str):
        # Ask the backend to tear down first, even if no runtime cache entry exists.
        try:
            self._backend_teardown_for_record(challenge_id)
        except Exception as e:
            self.logger.warning(f"Teardown backend failed for {challenge_id}: {e}")

        self._clear_runtime_state(challenge_id, drop_runtime_args=True)

    def close(self):
        """Close the manager and clean up any running challenges."""
        for cid in list(self._runtime_cache.keys()):
            try:
                self.teardown(cid)
            except Exception as e:
                self.logger.warning(f"Close teardown failed for {cid}: {e}")
        try:
            self.backend.cleanup()
        except Exception as e:
            self.logger.warning(f"Backend cleanup failed: {e}")

    def _apply_runtime_record(self, challenge_id: str, record: dict, meta: dict | None = None) -> dict:
        result = meta or self.challenges[challenge_id]
        self._runtime_cache[challenge_id] = record

        result["target_status"] = "running" if record["type"] == "dynamic" else "static"
        result["target_info"] = deepcopy(record.get("services", {}))
        result["runtime"] = self._build_runtime_metadata(record, result)

        if record["type"] == "static":
            result["message"] = f"Files at {record['work_dir']}"
        else:
            result["message"] = "Service Started."
            if self.config.run_mode == "remote" and self.config.use_ssh_tunnel:
                result["message"] += " (SSH Tunneled to Localhost)"

        self.challenges[challenge_id] |= result
        return result

    def _build_runtime_metadata(self, record: dict, challenge: dict) -> dict[str, Any]:
        source_fields = dict(challenge.get("source_fields", {}) or {})
        scoring = dict(record.get("scoring", {}) or source_fields.get("runtime_scoring", {}) or {})
        debug = dict(record.get("debug", {}) or {})
        network_debug = dict(debug.get("network", {}) or {})
        runtime: dict[str, Any] = {
            "run_id": record.get("run_id"),
            "project_name": record.get("project_name"),
            "network_name": record.get("network_name") or self.config.network_name,
            "network_subnet": record.get("network_subnet") or network_debug.get("subnet"),
            "network_gateway": record.get("network_gateway") or network_debug.get("gateway"),
            "scoring": scoring,
            "debug": debug,
        }
        return runtime

    def _backend_teardown_for_record(self, challenge_id: str) -> None:
        record = self._runtime_cache.get(challenge_id, {}) or {}
        run_id = record.get("run_id")
        try:
            self.backend.teardown(challenge_id, run_id=run_id)
        except TypeError:
            self.backend.teardown(challenge_id)

    def _clear_runtime_state(self, challenge_id: str, drop_runtime_args: bool) -> None:
        if challenge_id in self._runtime_cache:
            del self._runtime_cache[challenge_id]
        runtime_args_cache = getattr(self, "_runtime_args_cache", None)
        if drop_runtime_args and runtime_args_cache is not None and challenge_id in runtime_args_cache:
            del runtime_args_cache[challenge_id]

        if challenge_id in self.challenges:
            try:
                self.challenges[challenge_id]["target_status"] = "stopped"
                self.challenges[challenge_id]["target_info"] = {}
                self.challenges[challenge_id]["runtime"] = {}
            except Exception:
                pass

    def remember_runtime_args(
        self,
        challenge_id: str,
        runtime_args: dict[str, Any] | None = None,
    ) -> None:
        cache = getattr(self, "_runtime_args_cache", None)
        if cache is None:
            cache = {}
            self._runtime_args_cache = cache
        cache[challenge_id] = deepcopy(dict(runtime_args or {}))

    def _resolve_runtime_args(
        self,
        challenge_id: str,
        runtime_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache = getattr(self, "_runtime_args_cache", None)
        if cache is None:
            cache = {}
            self._runtime_args_cache = cache

        if runtime_args is not None:
            resolved = deepcopy(dict(runtime_args))
            cache[challenge_id] = resolved
            return deepcopy(resolved)

        return deepcopy(cache.get(challenge_id, {}))

    def _load_metadata(self) -> dict:
        registry = getattr(self, "benchmark_registry", None) or build_default_registry()
        sources = getattr(self, "benchmark_sources", None) or resolve_benchmark_sources(self.config)

        mapping = registry.discover_all(sources)
        for challenge in mapping.values():
            challenge["full_path"] = str(Path(challenge["full_path"]).resolve())
            challenge["benchmark"] = challenge.get("benchmark") or challenge.get("benchmark_name", "")

        return mapping
