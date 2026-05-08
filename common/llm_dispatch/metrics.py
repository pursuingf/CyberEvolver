"""Metric event writer + record builders + dispatcher-summary formatter."""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional

if TYPE_CHECKING:
    from .dispatcher import DispatcherScheduler, LLMDispatchRequest


DEFAULT_PERSISTED_METRIC_EVENTS = frozenset({"retry", "fail", "fatal_outage"})


class JSONLDispatcherMetricsWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(dict(record), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def should_persist_metric_event(event: str) -> bool:
    return str(event) in DEFAULT_PERSISTED_METRIC_EVENTS


def build_dispatcher_metric_record(
    event: str,
    request: "LLMDispatchRequest",
    queue_depth: Optional[int],
    inflight_total: Optional[int],
    inflight_lane: Optional[int],
    attempt: Optional[int] = None,
    latency_s: Optional[float] = None,
    status_code: Optional[int] = None,
    error_type: Optional[str] = None,
    error_kind: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    now = time.time()
    meta = dict(request.metadata or {})
    record: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "ts_unix": now,
        "event": event,
        "request_id": request.request_id,
        "lane": request.lane or "global",
        "chal_id": meta.get("chal_id"),
        "component": meta.get("component"),
        "node_id": meta.get("node_id"),
        "sample_id": meta.get("sample_id"),
        "llm_role": meta.get("llm_role"),
        "model": request.model,
        "is_large_request": bool(request.is_large_request),
        "estimated_total_tokens": int(request.estimated_total_tokens or 0),
        "queue_depth": queue_depth,
        "inflight_total": inflight_total,
        "inflight_lane": inflight_lane,
    }
    if attempt is not None:
        record["attempt"] = int(attempt)
    if latency_s is not None:
        record["latency_s"] = round(float(latency_s), 6)
    if status_code is not None:
        record["status_code"] = int(status_code)
    if error_type is not None:
        record["error_type"] = error_type
    if error_kind is not None:
        record["error_kind"] = error_kind
    if error_message is not None:
        record["error_message"] = error_message
    return record


def format_dispatcher_summary(
    snapshot: Mapping[str, Any],
    counters: Mapping[str, int],
    top_n: int = 3,
    large_stats: Optional[Mapping[str, Any]] = None,
) -> str:
    pending_total = int(snapshot.get("pending_total", 0) or 0)
    inflight_total = int(snapshot.get("inflight_total", 0) or 0)
    pending_by_lane = dict(snapshot.get("pending_by_lane", {}) or {})
    inflight_by_lane = dict(snapshot.get("inflight_by_lane", {}) or {})

    lane_names = sorted(
        set(pending_by_lane) | set(inflight_by_lane),
        key=lambda lane: (
            -(int(pending_by_lane.get(lane, 0) or 0) + int(inflight_by_lane.get(lane, 0) or 0)),
            -int(pending_by_lane.get(lane, 0) or 0),
            lane,
        ),
    )
    top_lane_parts = []
    for lane in lane_names[: max(0, int(top_n))]:
        top_lane_parts.append(
            f"{lane}(p={int(pending_by_lane.get(lane, 0) or 0)},i={int(inflight_by_lane.get(lane, 0) or 0)})"
        )
    top_lanes = ", ".join(top_lane_parts) if top_lane_parts else "-"

    line = (
        "dispatcher summary "
        f"pending={pending_total} "
        f"inflight={inflight_total} "
        f"enqueued={int(counters.get('enqueued', 0) or 0)} "
        f"dispatched={int(counters.get('dispatched', 0) or 0)} "
        f"completed={int(counters.get('completed', 0) or 0)} "
        f"failed={int(counters.get('failed', 0) or 0)} "
        f"retried={int(counters.get('retried', 0) or 0)} "
        f"top_lanes=[{top_lanes}]"
    )
    if large_stats:
        line += (
            f" large_inflight={int(large_stats.get('inflight', 0) or 0)}"
            f" large_finished={int(large_stats.get('finished', 0) or 0)}"
            f" large_avg_latency={float(large_stats.get('avg_latency_s', 0.0) or 0.0):.2f}s"
            f" large_rps={float(large_stats.get('rps', 0.0) or 0.0):.2f}"
        )
    return line


def record_enqueue_for_dispatcher(
    scheduler: "DispatcherScheduler",
    counters: Counter[str],
    request: "LLMDispatchRequest",
    emit_metric: Any,
) -> Dict[str, Any]:
    scheduler.enqueue(request)
    snapshot = scheduler.snapshot()
    counters["enqueued"] += 1
    emit_metric(
        event="enqueue",
        request=request,
        queue_depth=snapshot["pending_total"],
        inflight_total=snapshot["inflight_total"],
        inflight_lane=int(snapshot["inflight_by_lane"].get(request.lane or "global", 0)),
    )
    return snapshot


def _append_dispatcher_summary_line(log_path: str | Path, message: str) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    line = f"{ts} | INFO    | dispatcher | {message}\n"
    with path.open("a", encoding="utf-8") as f:
        try:
            import fcntl  # type: ignore

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            fcntl = None  # type: ignore
        try:
            f.write(line)
            f.flush()
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
