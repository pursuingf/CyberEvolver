from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque, Dict, Optional, TYPE_CHECKING

from .errors import (
    _is_breaker_relevant_result,
    _is_non200_breaker_result,
    format_dispatcher_fatal_message,
)

if TYPE_CHECKING:
    from .dispatcher import LLMDispatchRequest, LLMDispatchResult


class DispatcherOutageDetector:
    def __init__(
        self,
        *,
        window_seconds: float = 30.0,
        non200_threshold: int = 20,
        total_fail_threshold: int = 30,
        min_success: int = 0,
        consecutive_fail_threshold: int = 15,
        fail_rate_threshold: float = 0.9,
        fail_rate_min_samples: int = 40,
        fail_rate_window_seconds: float = 60.0,
        enabled: bool = True,
    ):
        self.enabled = bool(enabled)
        self.window_seconds = max(1.0, float(window_seconds))
        self.fail_rate_window_seconds = max(self.window_seconds, float(fail_rate_window_seconds))
        self.non200_threshold = max(1, int(non200_threshold))
        self.total_fail_threshold = max(1, int(total_fail_threshold))
        self.min_success = max(0, int(min_success))
        self.consecutive_fail_threshold = max(1, int(consecutive_fail_threshold))
        self.fail_rate_threshold = max(0.0, float(fail_rate_threshold))
        self.fail_rate_min_samples = max(1, int(fail_rate_min_samples))
        self._events: Deque[Dict[str, Any]] = deque()
        self._consecutive_failures = 0
        self._tripped = False

    def reset(self) -> None:
        self._events.clear()
        self._consecutive_failures = 0
        self._tripped = False

    def _prune(self, now: float) -> None:
        cutoff = now - self.fail_rate_window_seconds
        while self._events and float(self._events[0]["ts"]) < cutoff:
            self._events.popleft()

    def _window_counts(self, now: float, window_seconds: float) -> Dict[str, int]:
        cutoff = now - window_seconds
        total_failures = 0
        non200_failures = 0
        successes = 0
        sample_count = 0
        for event in self._events:
            if float(event["ts"]) < cutoff:
                continue
            if event["breaker_failure"] or event["success"]:
                sample_count += 1
            if event["breaker_failure"]:
                total_failures += 1
            if event["non200_failure"]:
                non200_failures += 1
            if event["success"]:
                successes += 1
        return {
            "total_failures": total_failures,
            "non200_failures": non200_failures,
            "successes": successes,
            "sample_count": sample_count,
        }

    def record_result(self, result: "LLMDispatchResult", *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not self.enabled or self._tripped:
            return None

        ts = float(now if now is not None else time.time())
        breaker_failure = _is_breaker_relevant_result(result)
        non200_failure = _is_non200_breaker_result(result)
        success = bool(result.ok)

        self._events.append(
            {
                "ts": ts,
                "breaker_failure": breaker_failure,
                "non200_failure": non200_failure,
                "success": success,
            }
        )
        self._prune(ts)

        if breaker_failure:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

        short_window = self._window_counts(ts, self.window_seconds)
        fail_rate_window = self._window_counts(ts, self.fail_rate_window_seconds)
        fail_rate = (
            float(fail_rate_window["total_failures"]) / float(fail_rate_window["sample_count"])
            if fail_rate_window["sample_count"] > 0
            else 0.0
        )

        reason = None
        if short_window["non200_failures"] >= self.non200_threshold:
            reason = "non200_threshold"
        elif (
            short_window["total_failures"] >= self.total_fail_threshold
            and short_window["successes"] <= self.min_success
        ):
            reason = "total_fail_threshold"
        elif self._consecutive_failures >= self.consecutive_fail_threshold:
            reason = "consecutive_fail_threshold"
        elif (
            fail_rate_window["sample_count"] >= self.fail_rate_min_samples
            and fail_rate >= self.fail_rate_threshold
        ):
            reason = "fail_rate_threshold"

        if reason is None:
            return None

        self._tripped = True
        payload = {
            "active": True,
            "detected_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
            "detected_ts_unix": ts,
            "reason": reason,
            "window_summary": {
                "window_seconds": self.window_seconds,
                "non200_failures": short_window["non200_failures"],
                "total_failures": short_window["total_failures"],
                "successes": short_window["successes"],
                "consecutive_failures": self._consecutive_failures,
                "fail_rate_window_seconds": self.fail_rate_window_seconds,
                "fail_rate_sample_count": fail_rate_window["sample_count"],
                "fail_rate": round(fail_rate, 6),
            },
            "last_error_type": result.error_type,
            "last_error_kind": result.error_kind,
            "last_status_code": result.status_code,
        }
        payload["message"] = format_dispatcher_fatal_message(payload)
        return payload


def build_outage_probe_request(trigger_request: "LLMDispatchRequest") -> "LLMDispatchRequest":
    from .dispatcher import LLMDispatchRequest
    probe_messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "who are u?"},
    ]
    probe_params = dict(trigger_request.request_params or {})
    if "max_tokens" in probe_params or "max_completion_tokens" not in probe_params:
        probe_params["max_tokens"] = 32
    if "max_completion_tokens" in probe_params:
        probe_params["max_completion_tokens"] = 32
    probe_params["temperature"] = 0.0
    return LLMDispatchRequest(
        request_id=f"{trigger_request.request_id}:probe",
        lane="dispatcher.probe",
        messages=probe_messages,
        model=trigger_request.model,
        endpoint=trigger_request.endpoint,
        api_key=trigger_request.api_key,
        request_params=probe_params,
        timeout_s=min(120.0, max(5.0, float(trigger_request.timeout_s or 30.0))),
        max_attempts=1,
        metadata={
            "chal_id": trigger_request.metadata.get("chal_id") if trigger_request.metadata else None,
            "component": "dispatcher.probe",
            "probe_for_component": trigger_request.metadata.get("component") if trigger_request.metadata else None,
            "probe_for_lane": trigger_request.lane,
        },
        estimated_input_tokens=0,
        estimated_total_tokens=0,
        is_large_request=False,
        large_request_delay_s=0.0,
    )


def confirm_fatal_outage_with_probe(
    detector: DispatcherOutageDetector,
    trigger_request: "LLMDispatchRequest",
    trigger_result: "LLMDispatchResult",
    probe_runner: Any,
) -> Optional[Dict[str, Any]]:
    fatal_payload = detector.record_result(trigger_result)
    if fatal_payload is None:
        return None

    probe_request = build_outage_probe_request(trigger_request)
    probe_result = probe_runner(probe_request)
    if probe_result.ok:
        detector.reset()
        return None

    confirmed_payload = dict(fatal_payload)
    confirmed_payload["probe_ok"] = False
    confirmed_payload["probe_error_type"] = probe_result.error_type
    confirmed_payload["probe_error_kind"] = probe_result.error_kind
    confirmed_payload["probe_status_code"] = probe_result.status_code
    confirmed_payload["probe_error_message"] = probe_result.error_message
    confirmed_payload["message"] = format_dispatcher_fatal_message(confirmed_payload)
    return confirmed_payload
