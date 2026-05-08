from __future__ import annotations

import json
import queue
import random
import threading
import time
import uuid
from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional

import httpx

from .messages import (
    _extract_text_content,
    _message_role_from_obj,
    _normalize_timeout_seconds,
    _serialize_tool_call,
    is_retryable_status,
    serialize_messages,
)
from .errors import (
    LLMDispatcherError,
    LLMDispatcherFatalError,
    _build_fatal_dispatch_result,
    _copy_fatal_state,
    _fatal_state_is_active,
    _is_breaker_relevant_result,
    _is_non200_breaker_result,
    _set_fatal_state,
    format_dispatcher_fatal_message,
)
from .outage import (
    DispatcherOutageDetector,
    build_outage_probe_request,
    confirm_fatal_outage_with_probe,
)
from .metrics import (
    DEFAULT_PERSISTED_METRIC_EVENTS,
    JSONLDispatcherMetricsWriter,
    _append_dispatcher_summary_line,
    build_dispatcher_metric_record,
    format_dispatcher_summary,
    record_enqueue_for_dispatcher,
    should_persist_metric_event,
)
from .request_payload import (
    _build_payload,
    _estimate_request_token_usage,
    _estimate_text_tokens,
)
from .remote_errors import (
    _build_remote_error_message,
    _classify_remote_error,
    _extract_remote_error_details,
    _flatten_remote_error_message,
    _summarize_response_body,
)


@dataclass
class LLMResponse:
    content: str
    usage_metadata: Dict[str, Any] = field(default_factory=dict)
    response_metadata: Dict[str, Any] = field(default_factory=dict)
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class LLMDispatchRequest:
    request_id: str
    lane: str
    messages: List[Dict[str, Any]]
    model: str
    endpoint: str
    api_key: str
    request_params: Dict[str, Any] = field(default_factory=dict)
    timeout_s: float = 300.0
    max_attempts: int = 5
    metadata: Dict[str, Any] = field(default_factory=dict)
    estimated_input_tokens: int = 0
    estimated_total_tokens: int = 0
    is_large_request: bool = False
    large_request_delay_s: float = 0.0


@dataclass
class LLMDispatchResult:
    ok: bool
    request_id: str
    response: Optional[LLMResponse] = None
    error_type: Optional[str] = None
    error_kind: Optional[str] = None
    error_message: Optional[str] = None
    attempt_count: int = 0
    latency_s: Optional[float] = None
    status_code: Optional[int] = None
    created_at: float = field(default_factory=time.time)


class LargeRequestThrottle:
    def __init__(self, *, monotonic_fn=time.monotonic, sleep_fn=time.sleep):
        self._monotonic_fn = monotonic_fn
        self._sleep_fn = sleep_fn
        self._lock = threading.Lock()
        self._last_started_at: Optional[float] = None

    def acquire(self, min_interval_s: float) -> None:
        interval = max(0.0, float(min_interval_s or 0.0))
        if interval <= 0.0:
            return
        with self._lock:
            now = float(self._monotonic_fn())
            if self._last_started_at is None:
                self._last_started_at = now
                return
            wait_s = max(0.0, self._last_started_at + interval - now)
            if wait_s > 0.0:
                self._sleep_fn(wait_s)
                now = float(self._monotonic_fn())
            self._last_started_at = now



class DispatcherScheduler:
    def __init__(self, max_inflight: int, max_inflight_per_lane: int):
        self.max_inflight = max(1, int(max_inflight))
        self.max_inflight_per_lane = max(1, int(max_inflight_per_lane))
        self._pending_by_lane: Dict[str, Deque[LLMDispatchRequest]] = {}
        self._lane_order: Deque[str] = deque()
        self._inflight_by_lane: Counter[str] = Counter()
        self._inflight_total = 0

    def enqueue(self, request: LLMDispatchRequest) -> None:
        lane = request.lane or "global"
        if lane not in self._pending_by_lane:
            self._pending_by_lane[lane] = deque()
            self._lane_order.append(lane)
        self._pending_by_lane[lane].append(request)

    def has_pending(self) -> bool:
        return any(self._pending_by_lane.values())

    def next_request(self) -> Optional[LLMDispatchRequest]:
        if self._inflight_total >= self.max_inflight:
            return None
        if not self._lane_order:
            return None

        lane_count = len(self._lane_order)
        for _ in range(lane_count):
            lane = self._lane_order[0]
            self._lane_order.rotate(-1)
            pending = self._pending_by_lane.get(lane)
            if not pending:
                self._pending_by_lane.pop(lane, None)
                try:
                    self._lane_order.remove(lane)
                except ValueError:
                    pass
                continue
            if self._inflight_by_lane[lane] >= self.max_inflight_per_lane:
                continue
            request = pending.popleft()
            if not pending:
                self._pending_by_lane.pop(lane, None)
                try:
                    self._lane_order.remove(lane)
                except ValueError:
                    pass
            self._inflight_total += 1
            self._inflight_by_lane[lane] += 1
            return request
        return None

    def mark_done(self, request: LLMDispatchRequest) -> None:
        lane = request.lane or "global"
        self._inflight_total = max(0, self._inflight_total - 1)
        self._inflight_by_lane[lane] -= 1
        if self._inflight_by_lane[lane] <= 0:
            self._inflight_by_lane.pop(lane, None)

    def snapshot(self) -> Dict[str, Any]:
        pending_by_lane = {
            lane: len(pending)
            for lane, pending in self._pending_by_lane.items()
            if pending
        }
        inflight_by_lane = {
            lane: int(count)
            for lane, count in self._inflight_by_lane.items()
            if int(count) > 0
        }
        return {
            "pending_total": sum(pending_by_lane.values()),
            "pending_by_lane": pending_by_lane,
            "inflight_total": int(self._inflight_total),
            "inflight_by_lane": inflight_by_lane,
        }

    def drain_pending(self) -> List[LLMDispatchRequest]:
        drained: List[LLMDispatchRequest] = []
        for lane in list(self._lane_order):
            pending = self._pending_by_lane.pop(lane, deque())
            while pending:
                drained.append(pending.popleft())
        self._lane_order.clear()
        return drained


def _perform_http_request(
    request: LLMDispatchRequest,
    observer: Optional[Any] = None,
    large_request_throttle: Optional[LargeRequestThrottle] = None,
) -> LLMDispatchResult:
    url = request.endpoint.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if request.api_key:
        headers["Authorization"] = f"Bearer {request.api_key}"

    delay_s = 1.0
    max_attempts = max(1, int(request.max_attempts))
    timeout_s = _normalize_timeout_seconds(request.timeout_s)
    last_error: Optional[BaseException] = None
    last_status_code: Optional[int] = None
    last_error_kind: Optional[str] = None
    started_at = time.time()
    attempt_count = 0

    for attempt in range(1, max_attempts + 1):
        attempt_count = attempt
        try:
            if attempt == 1 and request.is_large_request and float(request.large_request_delay_s or 0.0) > 0:
                if large_request_throttle is not None:
                    large_request_throttle.acquire(float(request.large_request_delay_s))
                else:
                    time.sleep(float(request.large_request_delay_s))
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.post(url, headers=headers, json=_build_payload(request))
            last_status_code = int(resp.status_code)
            if is_retryable_status(resp.status_code):
                raise httpx.HTTPStatusError(
                    f"retryable status {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                details = _extract_remote_error_details(resp)
                last_error_kind = details["error_kind"]
                if last_error_kind.startswith("request_") or last_error_kind == "rate_limited":
                    return LLMDispatchResult(
                        ok=False,
                        request_id=request.request_id,
                        error_type="RemoteRequestError",
                        error_kind=last_error_kind,
                        error_message=details["error_message"],
                        attempt_count=attempt,
                        latency_s=time.time() - started_at,
                        status_code=last_status_code,
                    )
                raise ValueError(
                    "LLM response missing choices"
                    f" | body_preview={details['body_preview']}"
                )
            message = choices[0].get("message") or {}
            usage = data.get("usage") or {}
            usage_metadata = {
                "input_tokens": int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
                "output_tokens": int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
                "total_tokens": int(usage.get("total_tokens", usage.get("total", 0)) or 0),
            }
            response_metadata = {
                "token_usage": usage,
                "raw_message": message,
                "raw_response_body": data,
            }
            # Extract tool_calls if present
            raw_tool_calls = message.get("tool_calls")
            tool_calls = None
            if raw_tool_calls:
                tool_calls = []
                for tc in raw_tool_calls:
                    tc_dict: Dict[str, Any] = {
                        "id": tc.get("id", ""),
                        "type": tc.get("type", "function"),
                    }
                    func = tc.get("function", {})
                    tc_dict["function"] = {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "{}"),
                    }
                    tool_calls.append(tc_dict)
            return LLMDispatchResult(
                ok=True,
                request_id=request.request_id,
                response=LLMResponse(
                    content=_extract_text_content(message.get("content", "")),
                    usage_metadata=usage_metadata,
                    response_metadata=response_metadata,
                    tool_calls=tool_calls,
                ),
                attempt_count=attempt,
                latency_s=time.time() - started_at,
                status_code=last_status_code,
            )
        except httpx.HTTPStatusError as exc:
            last_error = exc
            status_code = exc.response.status_code if exc.response is not None else -1
            last_status_code = status_code
            details = _extract_remote_error_details(exc.response) if exc.response is not None else None
            last_error_kind = (
                details["error_kind"] if details is not None else ("rate_limited" if status_code == 429 else None)
            )
            error_message = details["error_message"] if details is not None else str(exc)
            if last_error_kind in {"request_context_limit", "request_parameter_invalid", "request_invalid"}:
                return LLMDispatchResult(
                    ok=False,
                    request_id=request.request_id,
                    error_type="RemoteRequestError",
                    error_kind=last_error_kind,
                    error_message=error_message,
                    attempt_count=attempt,
                    latency_s=time.time() - started_at,
                    status_code=last_status_code,
                )
            should_retry = attempt < max_attempts and is_retryable_status(status_code)
            if should_retry and observer is not None:
                observer(
                    "retry",
                    request,
                    {
                        "attempt": attempt,
                        "status_code": status_code,
                        "error_type": type(exc).__name__,
                        "error_kind": last_error_kind,
                        "error_message": error_message,
                    },
                )
            if attempt >= max_attempts or not is_retryable_status(status_code):
                last_error = RuntimeError(error_message)
                break
        except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
            last_error = exc
            last_error_kind = "request_timeout" if isinstance(exc, httpx.TimeoutException) else "connection_error"
            if attempt < max_attempts and observer is not None:
                observer(
                    "retry",
                    request,
                    {
                        "attempt": attempt,
                        "status_code": last_status_code,
                        "error_type": type(exc).__name__,
                        "error_kind": last_error_kind,
                        "error_message": str(exc),
                    },
                )
            if attempt >= max_attempts:
                break
        except Exception as exc:  # pragma: no cover - defensive catch for unexpected payloads
            last_error = exc
            break

        sleep_s = min(delay_s, 20.0) + random.uniform(0.0, 0.25 * delay_s)
        time.sleep(sleep_s)
        delay_s = min(delay_s * 2.0, 20.0)

    err = last_error or RuntimeError("unknown dispatcher transport failure")
    return LLMDispatchResult(
        ok=False,
        request_id=request.request_id,
        error_type=type(err).__name__,
        error_kind=last_error_kind,
        error_message=str(err),
        attempt_count=attempt_count,
        latency_s=time.time() - started_at,
        status_code=last_status_code,
    )


def _dispatcher_process_main(
    request_queue: Any,
    response_store: Any,
    fatal_state: Any,
    stop_event: Any,
    max_inflight: int,
    max_inflight_per_lane: int,
    response_ttl_s: float,
    metrics_path: Optional[str],
    summary_log_path: Optional[str],
    summary_interval_s: float,
    fatal_window_seconds: float,
    fatal_non200_threshold: int,
    fatal_total_fail_threshold: int,
    fatal_min_success: int,
    fatal_consecutive_fails: int,
    fatal_fail_rate_threshold: float,
    fatal_fail_rate_min_samples: int,
    disable_fatal_breaker: bool,
) -> None:
    scheduler = DispatcherScheduler(max_inflight=max_inflight, max_inflight_per_lane=max_inflight_per_lane)
    detector = DispatcherOutageDetector(
        window_seconds=fatal_window_seconds,
        non200_threshold=fatal_non200_threshold,
        total_fail_threshold=fatal_total_fail_threshold,
        min_success=fatal_min_success,
        consecutive_fail_threshold=fatal_consecutive_fails,
        fail_rate_threshold=fatal_fail_rate_threshold,
        fail_rate_min_samples=fatal_fail_rate_min_samples,
        enabled=not disable_fatal_breaker,
    )
    future_map: Dict[Future, LLMDispatchRequest] = {}
    stop_requested = False
    last_gc = time.time()
    last_summary = time.time()
    metrics_writer = JSONLDispatcherMetricsWriter(metrics_path) if metrics_path else None
    counters: Counter[str] = Counter()
    counters_lock = threading.Lock()
    dispatcher_started_at = time.time()
    large_latency_total_s = 0.0
    large_request_throttle = LargeRequestThrottle()

    def emit_metric(
        event: str,
        request: LLMDispatchRequest,
        queue_depth: Optional[int],
        inflight_total: Optional[int],
        inflight_lane: Optional[int],
        attempt: Optional[int] = None,
        latency_s: Optional[float] = None,
        status_code: Optional[int] = None,
        error_type: Optional[str] = None,
        error_kind: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if metrics_writer is None or not should_persist_metric_event(event):
            return
        metrics_writer.write(
            build_dispatcher_metric_record(
                event=event,
                request=request,
                queue_depth=queue_depth,
                inflight_total=inflight_total,
                inflight_lane=inflight_lane,
                attempt=attempt,
                latency_s=latency_s,
                status_code=status_code,
                error_type=error_type,
                error_kind=error_kind,
                error_message=error_message,
            )
        )

    def record_retry(event: str, request: LLMDispatchRequest, payload: Mapping[str, Any]) -> None:
        if event != "retry":
            return
        with counters_lock:
            counters["retried"] += 1
        emit_metric(
            event="retry",
            request=request,
            queue_depth=None,
            inflight_total=None,
            inflight_lane=None,
            attempt=payload.get("attempt"),
            status_code=payload.get("status_code"),
            error_type=payload.get("error_type"),
            error_kind=payload.get("error_kind"),
            error_message=payload.get("error_message"),
        )

    def fail_request_due_to_fatal(request: LLMDispatchRequest, snapshot: Mapping[str, Any]) -> None:
        fatal_payload = _copy_fatal_state(fatal_state)
        result = _build_fatal_dispatch_result(request.request_id, fatal_payload)
        response_store[request.request_id] = result
        with counters_lock:
            counters["failed"] += 1
        emit_metric(
            event="fail",
            request=request,
            queue_depth=snapshot.get("pending_total"),
            inflight_total=snapshot.get("inflight_total"),
            inflight_lane=int(snapshot.get("inflight_by_lane", {}).get(request.lane or "global", 0)),
            error_type=result.error_type,
            error_kind=result.error_kind,
            error_message=result.error_message,
        )

    def activate_fatal_outage(trigger_request: LLMDispatchRequest, trigger_result: LLMDispatchResult, snapshot: Mapping[str, Any]) -> None:
        nonlocal stop_requested
        def run_outage_probe(probe_request: LLMDispatchRequest) -> LLMDispatchResult:
            if summary_log_path:
                _append_dispatcher_summary_line(
                    summary_log_path,
                    "LLM dispatcher outage probe starting after breaker threshold hit.",
                )
            probe_result = _perform_http_request(probe_request)
            if summary_log_path:
                if probe_result.ok:
                    _append_dispatcher_summary_line(
                        summary_log_path,
                        "LLM dispatcher outage probe succeeded; cancelling fatal outage and resetting detector.",
                    )
                else:
                    _append_dispatcher_summary_line(
                        summary_log_path,
                        "LLM dispatcher outage probe failed; confirming fatal outage. "
                        f"error_type={probe_result.error_type} "
                        f"error_kind={probe_result.error_kind} "
                        f"status_code={probe_result.status_code}",
                    )
            return probe_result

        fatal_payload = confirm_fatal_outage_with_probe(
            detector,
            trigger_request,
            trigger_result,
            probe_runner=run_outage_probe,
        )
        if fatal_payload is None or _fatal_state_is_active(fatal_state):
            return

        _set_fatal_state(fatal_state, fatal_payload)
        emit_metric(
            event="fatal_outage",
            request=trigger_request,
            queue_depth=snapshot.get("pending_total"),
            inflight_total=snapshot.get("inflight_total"),
            inflight_lane=int(snapshot.get("inflight_by_lane", {}).get(trigger_request.lane or "global", 0)),
            status_code=trigger_result.status_code,
            error_type=trigger_result.error_type,
            error_kind=trigger_result.error_kind,
            error_message=fatal_payload.get("message"),
        )
        if summary_log_path:
            _append_dispatcher_summary_line(
                summary_log_path,
                f"LLM dispatcher fatal outage detected. {fatal_payload.get('message')}",
            )

        pending_requests = scheduler.drain_pending()
        drained_snapshot = scheduler.snapshot()
        for pending_request in pending_requests:
            fail_request_due_to_fatal(pending_request, drained_snapshot)
        stop_requested = True

    with ThreadPoolExecutor(max_workers=max_inflight) as executor:
        while True:
            completed_any = False
            for future, request in list(future_map.items()):
                if not future.done():
                    continue
                completed_any = True
                future_map.pop(future, None)
                scheduler.mark_done(request)
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive guard
                    result = LLMDispatchResult(
                        ok=False,
                        request_id=request.request_id,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                response_store[request.request_id] = result
                snapshot = scheduler.snapshot()
                inflight_lane = int(snapshot["inflight_by_lane"].get(request.lane or "global", 0))
                if request.is_large_request:
                    counters["large_finished"] += 1
                    if result.latency_s is not None:
                        large_latency_total_s += float(result.latency_s)
                if result.ok:
                    detector.record_result(result)
                    with counters_lock:
                        counters["completed"] += 1
                    emit_metric(
                        event="complete",
                        request=request,
                        queue_depth=snapshot["pending_total"],
                        inflight_total=snapshot["inflight_total"],
                        inflight_lane=inflight_lane,
                        attempt=result.attempt_count,
                        latency_s=result.latency_s,
                        status_code=result.status_code,
                    )
                else:
                    with counters_lock:
                        counters["failed"] += 1
                    emit_metric(
                        event="fail",
                        request=request,
                        queue_depth=snapshot["pending_total"],
                        inflight_total=snapshot["inflight_total"],
                        inflight_lane=inflight_lane,
                        attempt=result.attempt_count,
                        latency_s=result.latency_s,
                        status_code=result.status_code,
                        error_type=result.error_type,
                        error_kind=result.error_kind,
                        error_message=result.error_message,
                    )
                    activate_fatal_outage(request, result, snapshot)

            while True:
                try:
                    item = request_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    stop_requested = True
                    continue
                if _fatal_state_is_active(fatal_state):
                    fail_request_due_to_fatal(item, scheduler.snapshot())
                    continue
                with counters_lock:
                    record_enqueue_for_dispatcher(
                        scheduler=scheduler,
                        counters=counters,
                        request=item,
                        emit_metric=emit_metric,
                    )

            dispatched_any = False
            while True:
                if _fatal_state_is_active(fatal_state):
                    break
                request = scheduler.next_request()
                if request is None:
                    break
                dispatched_any = True
                snapshot = scheduler.snapshot()
                with counters_lock:
                    counters["dispatched"] += 1
                emit_metric(
                    event="dispatch",
                    request=request,
                    queue_depth=snapshot["pending_total"],
                    inflight_total=snapshot["inflight_total"],
                    inflight_lane=int(snapshot["inflight_by_lane"].get(request.lane or "global", 0)),
                )
                future_map[executor.submit(_perform_http_request, request, record_retry, large_request_throttle)] = request

            now = time.time()
            if now - last_gc >= 60:
                for key, result in list(response_store.items()):
                    if now - float(getattr(result, "created_at", now)) > response_ttl_s:
                        try:
                            response_store.pop(key, None)
                        except Exception:
                            pass
                last_gc = now

            if summary_log_path and (now - last_summary >= max(1.0, float(summary_interval_s))):
                snapshot = scheduler.snapshot()
                large_inflight = sum(1 for active_request in future_map.values() if active_request.is_large_request)
                large_finished = int(counters.get("large_finished", 0) or 0)
                large_avg_latency = (large_latency_total_s / large_finished) if large_finished else 0.0
                elapsed = max(now - dispatcher_started_at, 1e-9)
                large_stats = {
                    "inflight": large_inflight,
                    "finished": large_finished,
                    "avg_latency_s": large_avg_latency,
                    "rps": large_finished / elapsed,
                }
                with counters_lock:
                    summary_line = format_dispatcher_summary(snapshot=snapshot, counters=dict(counters), large_stats=large_stats)
                _append_dispatcher_summary_line(summary_log_path, summary_line)
                last_summary = now

            if stop_event.is_set():
                stop_requested = True

            if stop_requested and not future_map and not scheduler.has_pending():
                if summary_log_path:
                    snapshot = scheduler.snapshot()
                    with counters_lock:
                        summary_line = format_dispatcher_summary(snapshot=snapshot, counters=dict(counters))
                    _append_dispatcher_summary_line(summary_log_path, summary_line)
                break

            if completed_any or dispatched_any:
                continue

            try:
                item = request_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if item is None:
                stop_requested = True
                continue
            if _fatal_state_is_active(fatal_state):
                fail_request_due_to_fatal(item, scheduler.snapshot())
                continue
            with counters_lock:
                record_enqueue_for_dispatcher(
                    scheduler=scheduler,
                    counters=counters,
                    request=item,
                    emit_metric=emit_metric,
                )


def _lane_from_meta(meta: Optional[Mapping[str, Any]]) -> str:
    if not meta:
        return "global"
    chal_id = meta.get("chal_id")
    if chal_id:
        return str(chal_id)
    lane = meta.get("lane")
    if lane:
        return str(lane)
    return "global"


@dataclass
class LLMClientStub:
    request_queue: Any
    response_store: Any
    model: str
    endpoint: str
    api_key: str
    fatal_state: Any = None
    request_params: Dict[str, Any] = field(default_factory=dict)
    timeout_s: float = 300.0
    max_attempts: int = 5
    submit_timeout_s: float = 30.0
    response_timeout_s: float = 7200.0
    poll_interval_s: float = 0.05
    large_request_threshold: int = 30000
    large_request_delay_s: float = 2.0

    accepts_dispatch_meta = True

    def invoke(self, messages: Iterable[Any], _dispatch_meta: Optional[Mapping[str, Any]] = None, tools: Optional[List[Dict[str, Any]]] = None, tool_choice: Optional[Any] = None):
        fatal_snapshot = _copy_fatal_state(self.fatal_state)
        if fatal_snapshot.get("active"):
            raise LLMDispatcherFatalError(format_dispatcher_fatal_message(fatal_snapshot))

        request_id = uuid.uuid4().hex
        serialized_messages = serialize_messages(messages)

        # Build request_params with optional tools/tool_choice
        params = dict(self.request_params or {})
        if tools is not None:
            params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = tool_choice

        estimate = _estimate_request_token_usage(serialized_messages, params, self.large_request_threshold)
        request = LLMDispatchRequest(
            request_id=request_id,
            lane=_lane_from_meta(_dispatch_meta),
            messages=serialized_messages,
            model=self.model,
            endpoint=self.endpoint,
            api_key=self.api_key,
            request_params=params,
            timeout_s=_normalize_timeout_seconds(self.timeout_s),
            max_attempts=self.max_attempts,
            metadata=dict(_dispatch_meta or {}),
            estimated_input_tokens=int(estimate["estimated_input_tokens"]),
            estimated_total_tokens=int(estimate["estimated_total_tokens"]),
            is_large_request=bool(estimate["is_large_request"]),
            large_request_delay_s=float(self.large_request_delay_s),
        )
        deadline = time.time() + max(1.0, float(self.response_timeout_s))

        while True:
            try:
                self.request_queue.put(request, timeout=self.submit_timeout_s)
                break
            except queue.Full:
                if time.time() >= deadline:
                    raise TimeoutError("Timed out enqueueing LLM request")

        while time.time() < deadline:
            if request_id in self.response_store:
                result = self.response_store.pop(request_id)
                if result.ok and result.response is not None:
                    return result.response
                if result.error_type == "LLMDispatcherFatalError":
                    raise LLMDispatcherFatalError(result.error_message or "dispatcher fatal outage detected")
                raise LLMDispatcherError(f"{result.error_type}: {result.error_message}")
            fatal_snapshot = _copy_fatal_state(self.fatal_state)
            if fatal_snapshot.get("active"):
                raise LLMDispatcherFatalError(format_dispatcher_fatal_message(fatal_snapshot))
            time.sleep(self.poll_interval_s)
        raise TimeoutError(f"Timed out waiting for dispatcher response for request {request_id}")


@dataclass
class LLMDispatcherHandle:
    request_queue: Any
    response_store: Any
    fatal_state: Any
    submit_timeout_s: float
    response_timeout_s: float
    poll_interval_s: float
    default_max_attempts: int
    default_timeout_s: float
    large_request_threshold: int
    large_request_delay_s: float

    def build_client(self, model_kwargs: Mapping[str, Any], **overrides: Any) -> LLMClientStub:
        params = dict(model_kwargs or {})
        params.update(overrides)

        endpoint = str(params.pop("openai_api_base", params.pop("base_url", "")))
        api_key = str(params.pop("openai_api_key", params.pop("api_key", "")))
        model = str(params.pop("model"))
        timeout_s = _normalize_timeout_seconds(params.pop("timeout", self.default_timeout_s), default=self.default_timeout_s)

        return LLMClientStub(
            request_queue=self.request_queue,
            response_store=self.response_store,
            fatal_state=self.fatal_state,
            model=model,
            endpoint=endpoint,
            api_key=api_key,
            request_params=params,
            timeout_s=timeout_s,
            max_attempts=self.default_max_attempts,
            submit_timeout_s=self.submit_timeout_s,
            response_timeout_s=self.response_timeout_s,
            poll_interval_s=self.poll_interval_s,
            large_request_threshold=self.large_request_threshold,
            large_request_delay_s=self.large_request_delay_s,
        )


class LLMDispatcherRuntime:
    def __init__(
        self,
        mp_context: Any,
        max_inflight: int = 4,
        max_inflight_per_lane: int = 2,
        default_timeout_s: float = 300.0,
        default_max_attempts: int = 5,
        submit_timeout_s: float = 30.0,
        response_timeout_s: float = 7200.0,
        poll_interval_s: float = 0.05,
        response_ttl_s: float = 3600.0,
        metrics_path: Optional[str | Path] = None,
        summary_log_path: Optional[str | Path] = None,
        summary_interval_s: float = 30.0,
        fatal_window_seconds: float = 30.0,
        fatal_non200_threshold: int = 20,
        fatal_total_fail_threshold: int = 30,
        fatal_min_success: int = 0,
        fatal_consecutive_fails: int = 15,
        fatal_fail_rate_threshold: float = 0.9,
        fatal_fail_rate_min_samples: int = 40,
        disable_fatal_breaker: bool = False,
        large_request_threshold: int = 30000,
        large_request_delay_s: float = 2.0,
    ):
        self._manager = mp_context.Manager()
        self._request_queue = self._manager.Queue()
        self._response_store = self._manager.dict()
        self._fatal_state = self._manager.dict({"active": False})
        self._stop_event = self._manager.Event()
        self._process = mp_context.Process(
            target=_dispatcher_process_main,
            args=(
                self._request_queue,
                self._response_store,
                self._fatal_state,
                self._stop_event,
                max_inflight,
                max_inflight_per_lane,
                response_ttl_s,
                str(metrics_path) if metrics_path else None,
                str(summary_log_path) if summary_log_path else None,
                float(summary_interval_s),
                float(fatal_window_seconds),
                int(fatal_non200_threshold),
                int(fatal_total_fail_threshold),
                int(fatal_min_success),
                int(fatal_consecutive_fails),
                float(fatal_fail_rate_threshold),
                int(fatal_fail_rate_min_samples),
                bool(disable_fatal_breaker),
            ),
            daemon=True,
        )
        self.handle = LLMDispatcherHandle(
            request_queue=self._request_queue,
            response_store=self._response_store,
            fatal_state=self._fatal_state,
            submit_timeout_s=submit_timeout_s,
            response_timeout_s=response_timeout_s,
            poll_interval_s=poll_interval_s,
            default_max_attempts=default_max_attempts,
            default_timeout_s=default_timeout_s,
            large_request_threshold=int(large_request_threshold),
            large_request_delay_s=float(large_request_delay_s),
        )

    def start(self) -> None:
        if not self._process.is_alive():
            _set_fatal_state(self._fatal_state, {"active": False})
            self._process.start()

    def shutdown(self, timeout_s: float = 10.0) -> None:
        self._stop_event.set()
        try:
            self._request_queue.put(None, timeout=0.1)
        except Exception:
            pass
        if self._process.is_alive():
            self._process.join(timeout=timeout_s)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1.0)
        try:
            self._manager.shutdown()
        except Exception:
            pass
