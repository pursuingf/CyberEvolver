from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

from common.agent_runtime.docker_env import DockerEnvironment, DockerEnvironmentConfig
from common.utils.container_paths import opaque_token


class GlobalDockerManager:
    def __init__(
        self,
        config: Dict,
        chal_id: str,
        logger: logging.Logger | None = None,
        default_runtime_args: Dict[str, Any] | None = None,
    ):
        self.logger = logger or logging.getLogger("DockerGlobal")
        self.config = DockerEnvironmentConfig(**config)
        self.config.run_args = ["--rm"]
        self.default_runtime_args = dict(default_runtime_args or {})
        self.cache_root = "/ctf/cache"
        self.run_root = "/ctf/run"
        self._shared_chal_id = chal_id
        self.env: DockerEnvironment | None = None
        self.runtime_coordinator = None
        self._leased_envs: list[DockerEnvironment] = []
        self._cached_ids_by_env: dict[str, set[str]] = {}

    def _build_container_name(self, chal_id: str, mode: str = "shared") -> str:
        token = "".join(ch if ch.isalnum() else "_" for ch in chal_id).strip("_").lower()
        if not token:
            token = "root"
        prefix = "agent_sandbox" if str(mode).strip().lower() == "exclusive" else "evo_tree"
        return f"{prefix}_{token}_{uuid.uuid4().hex[:6]}"

    def _cache_key(self, env: DockerEnvironment) -> str:
        return str(getattr(env, "container_id", None) or env.config.container_name or id(env))

    def _prepare_roots(self, env: DockerEnvironment) -> None:
        env.mkdir(self.cache_root)
        env.mkdir(self.run_root)

    def _benchmark_family(self, chal_data: Dict[str, Any]) -> str:
        return str(chal_data.get("benchmark_family", "") or "").lower()

    def _runtime_args(self, runtime_args: Dict[str, Any] | None = None) -> Dict[str, Any]:
        merged = dict(self.default_runtime_args)
        if runtime_args:
            merged.update(runtime_args)
        return merged

    def _ensure_shared_environment(self, chal_id: str | None = None) -> DockerEnvironment:
        if self.env is not None:
            return self.env

        config_data = asdict(self.config)
        if not config_data.get("container_name"):
            config_data["container_name"] = self._build_container_name(chal_id or self._shared_chal_id, mode="shared")
        env = DockerEnvironment(config=DockerEnvironmentConfig(**config_data), logger=self.logger)
        if self.runtime_coordinator is not None:
            env.runtime_coordinator = self.runtime_coordinator
        self._prepare_roots(env)
        self.env = env
        self._cached_ids_by_env[self._cache_key(env)] = set()
        return env

    def _sandbox_policy(self, chal_data: Dict[str, Any], runtime_args: Dict[str, Any] | None = None) -> str:
        resolved_runtime_args = self._runtime_args(runtime_args)
        policy = str(resolved_runtime_args.get("sandbox_policy", "") or "").strip().lower()
        if policy:
            return policy
        if self._benchmark_family(chal_data) == "cvebench":
            return "exclusive"
        return "shared"

    def set_runtime_coordinator(self, coordinator: Any) -> None:
        self.runtime_coordinator = coordinator
        if self.env is not None:
            self.env.runtime_coordinator = coordinator

    def _new_environment(self, chal_id: str) -> DockerEnvironment:
        config_data = asdict(self.config)
        config_data["container_name"] = self._build_container_name(chal_id, mode="exclusive")
        env = DockerEnvironment(config=DockerEnvironmentConfig(**config_data), logger=self.logger)
        if self.runtime_coordinator is not None:
            env.runtime_coordinator = self.runtime_coordinator
        self._prepare_roots(env)
        self._leased_envs.append(env)
        self._cached_ids_by_env[self._cache_key(env)] = set()
        return env

    def allocate_environment(
        self,
        chal_data: Dict[str, Any],
        chal_id: str,
        runtime_args: Dict[str, Any] | None = None,
    ) -> DockerEnvironment:
        if self._sandbox_policy(chal_data, runtime_args=runtime_args) == "exclusive":
            return self._new_environment(chal_id)
        return self._ensure_shared_environment(chal_id)

    def release_environment(self, env: DockerEnvironment) -> None:
        if env is self.env:
            return
        cache_key = self._cache_key(env)
        try:
            env.cleanup(force=True)
        finally:
            self._cached_ids_by_env.pop(cache_key, None)
            self._leased_envs = [leased for leased in self._leased_envs if leased is not env]

    def prepare_challenge_cache(self, chal_id: str, chal_data: Dict[str, Any], env: DockerEnvironment | None = None) -> str:
        container_cache_path = f"{self.cache_root}/{opaque_token(chal_id)}"
        if env is None:
            if self._sandbox_policy(chal_data, runtime_args=None) == "exclusive":
                return container_cache_path
            env = self._ensure_shared_environment(chal_id)

        cache_key = self._cache_key(env)
        cached_ids = self._cached_ids_by_env.setdefault(cache_key, set())
        if chal_id in cached_ids or env.exists(container_cache_path):
            cached_ids.add(chal_id)
            return container_cache_path

        self.logger.info("Uploading resources for challenge: %s", chal_id)
        local_tmp_dir = Path("/tmp") / f"ctf_upload_{opaque_token(chal_id)}_{uuid.uuid4().hex[:6]}"
        local_tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            for rel_path in chal_data.get("files", []):
                src = Path(chal_data["full_path"]) / rel_path
                dst = local_tmp_dir / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                elif src.exists():
                    shutil.copy2(src, dst)

            env.mkdir(container_cache_path)
            env.cp_to_container(str(local_tmp_dir), "/ctf/tmp_upload")
            env.execute(f"cp -r /ctf/tmp_upload/* {container_cache_path}/")
            env.execute("rm -rf /ctf/tmp_upload")
        finally:
            shutil.rmtree(local_tmp_dir, ignore_errors=True)

        cached_ids.add(chal_id)
        return container_cache_path

    def cleanup(self) -> None:
        for env in list(self._leased_envs):
            try:
                env.cleanup(force=True)
            except Exception as exc:
                self.logger.warning("Failed to cleanup leased sandbox %s: %s", getattr(env, "container_id", None), exc)
        self._leased_envs.clear()
        self._cached_ids_by_env.clear()
        if self.env is not None:
            self.env.cleanup(force=True)
            self.env = None
