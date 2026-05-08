import logging
import socket
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class TargetRecoveryResult:
    recovered: bool
    target_changed: bool
    target_info: dict[str, Any]
    target_status: str


class ChallengeRuntimeCoordinator:
    def __init__(
        self,
        challenge_client,
        challenge_id: str,
        logger: logging.Logger | None = None,
        probe_target_info: Callable[[dict[str, Any]], bool] | None = None,
        warmup_probe_attempts: int = 3,
        warmup_probe_interval_s: float = 3.0,
    ) -> None:
        self.challenge_client = challenge_client
        self.challenge_id = challenge_id
        self.logger = logger or logging.getLogger("ChallengeRuntimeCoordinator")
        self._probe_target_info_fn = probe_target_info or self._default_probe_target_info
        self.warmup_probe_attempts = max(0, int(warmup_probe_attempts))
        self.warmup_probe_interval_s = max(0.0, float(warmup_probe_interval_s))
        self._lock = threading.Lock()
        self._latest_target_info: dict[str, Any] = {}
        self._latest_runtime: dict[str, Any] = {}

    def ensure_target_available(self, chal_data: dict[str, Any]) -> TargetRecoveryResult:
        target_info = deepcopy(chal_data.get("target_info", {}) or {})
        if self._probe_target_info(target_info):
            self._latest_target_info = deepcopy(target_info)
            self._latest_runtime = deepcopy(chal_data.get("runtime", {}) or {})
            return TargetRecoveryResult(
                recovered=False,
                target_changed=False,
                target_info=target_info,
                target_status=chal_data.get("target_status", ""),
            )
        if self._wait_for_target_ready(target_info):
            self._latest_target_info = deepcopy(target_info)
            self._latest_runtime = deepcopy(chal_data.get("runtime", {}) or {})
            self.logger.info(
                "Target for %s became reachable during warmup wait; skipping recreate",
                self.challenge_id,
            )
            return TargetRecoveryResult(
                recovered=False,
                target_changed=False,
                target_info=target_info,
                target_status=chal_data.get("target_status", ""),
            )
        return self.recover_and_refresh(chal_data, reason="target unavailable")

    def _wait_for_target_ready(self, target_info: dict[str, Any]) -> bool:
        if self.warmup_probe_attempts <= 0:
            return False

        for attempt in range(self.warmup_probe_attempts):
            if self.warmup_probe_interval_s > 0:
                time.sleep(self.warmup_probe_interval_s)
            if self._probe_target_info(target_info):
                return True
            if attempt + 1 < self.warmup_probe_attempts:
                self.logger.info(
                    "Target for %s still unavailable during warmup wait (%d/%d)",
                    self.challenge_id,
                    attempt + 1,
                    self.warmup_probe_attempts,
                )
        return False

    def recover_and_refresh(
        self,
        chal_data: dict[str, Any],
        reason: str,
    ) -> TargetRecoveryResult:
        old_target_info = deepcopy(chal_data.get("target_info", {}) or {})
        old_runtime = deepcopy(chal_data.get("runtime", {}) or {})
        with self._lock:
            latest_target_info = deepcopy(self._latest_target_info or old_target_info)
            if self._probe_target_info(latest_target_info):
                self._apply_runtime_state(
                    chal_data,
                    chal_data.get("target_status", ""),
                    latest_target_info,
                    deepcopy(self._latest_runtime or old_runtime),
                )
                target_changed = old_target_info != latest_target_info
                return TargetRecoveryResult(
                    recovered=target_changed,
                    target_changed=target_changed,
                    target_info=deepcopy(latest_target_info),
                    target_status=chal_data.get("target_status", ""),
                )

            self.logger.warning(
                "Recovering target for %s due to %s",
                self.challenge_id,
                reason,
            )
            refreshed = self.challenge_client.refresh_challenge_data(
                self.challenge_id,
                force_recreate=True,
            )
            new_target_info = deepcopy(refreshed.get("target_info", {}) or {})
            new_target_status = refreshed.get("target_status", chal_data.get("target_status", ""))
            new_runtime = deepcopy(refreshed.get("runtime", {}) or {})
            self._latest_target_info = deepcopy(new_target_info)
            self._latest_runtime = deepcopy(new_runtime)
            self._apply_runtime_state(chal_data, new_target_status, new_target_info, new_runtime)
            return TargetRecoveryResult(
                recovered=True,
                target_changed=old_target_info != new_target_info,
                target_info=new_target_info,
                target_status=new_target_status,
            )

    def _apply_runtime_state(
        self,
        chal_data: dict[str, Any],
        target_status: str,
        target_info: dict[str, Any],
        runtime: dict[str, Any],
    ) -> None:
        chal_data["target_status"] = target_status
        chal_data["target_info"] = deepcopy(target_info)
        chal_data["runtime"] = deepcopy(runtime)

    def _probe_target_info(self, target_info: dict[str, Any]) -> bool:
        return self._probe_target_info_fn(target_info)

    def _default_probe_target_info(self, target_info: dict[str, Any]) -> bool:
        if not target_info:
            return True

        for svc in target_info.values():
            host = svc.get("host")
            port = svc.get("port")
            if not host or port is None:
                continue
            try:
                with socket.create_connection((host, int(port)), timeout=1.0):
                    continue
            except (socket.timeout, ConnectionRefusedError, OSError):
                return False
        return True
