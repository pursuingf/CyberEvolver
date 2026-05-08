from __future__ import annotations

import json
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


def _as_mapping(obj: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(obj, Mapping):
        return obj
    return None


def _first_int(*vals: Any) -> Optional[int]:
    for v in vals:
        if v is None:
            continue
        try:
            return int(v)
        except Exception:
            continue
    return None


def extract_token_usage(resp: Any) -> Dict[str, Optional[int]]:
    """
    Best-effort extraction of token usage from LangChain responses.

    Common places:
    - resp.usage_metadata: {'input_tokens'/'output_tokens'/'total_tokens'} (newer LC)
    - resp.response_metadata['token_usage']: {'prompt_tokens'/'completion_tokens'/'total_tokens'} (OpenAI-style)
    - resp.response_metadata['usage']: same as above (some proxies)
    """
    usage: Dict[str, Any] = {}

    usage_metadata = _as_mapping(getattr(resp, "usage_metadata", None))
    if usage_metadata:
        usage.update(dict(usage_metadata))

    response_metadata = _as_mapping(getattr(resp, "response_metadata", None))
    if response_metadata:
        for key in ("token_usage", "usage"):
            token_usage = _as_mapping(response_metadata.get(key))
            if token_usage:
                # Don't overwrite usage_metadata if already present
                for k, v in token_usage.items():
                    usage.setdefault(k, v)

    input_tokens = _first_int(
        usage.get("input_tokens"),
        usage.get("prompt_tokens"),
        usage.get("promptTokens"),
        usage.get("inputTokens"),
    )
    output_tokens = _first_int(
        usage.get("output_tokens"),
        usage.get("completion_tokens"),
        usage.get("completionTokens"),
        usage.get("outputTokens"),
    )
    total_tokens = _first_int(
        usage.get("total_tokens"),
        usage.get("totalTokens"),
        (input_tokens + output_tokens) if (input_tokens is not None and output_tokens is not None) else None,
    )

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


@dataclass(frozen=True)
class TokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class JSONLUsageLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


class TokenBudgetExceededError(RuntimeError):
    pass


class FileTokenBudget:
    """
    Cross-process token budget tracker using a JSON file + OS file lock.

    Designed for ProcessPoolExecutor ('spawn') workers to share a single budget
    without relying on in-memory shared state.
    """

    def __init__(
        self,
        path: str | Path,
        max_total_tokens: Optional[int] = None,
        max_chal_tokens: Optional[int] = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_total_tokens = int(max_total_tokens) if max_total_tokens is not None else None
        self.max_chal_tokens = int(max_chal_tokens) if max_chal_tokens is not None else None

    def _default_state(self) -> Dict[str, Any]:
        return {
            "max_total_tokens": self.max_total_tokens,
            "max_chal_tokens": self.max_chal_tokens,
            "used": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "by_challenge": {},
            "updated_at": None,
        }

    @staticmethod
    def _lock_file(f):
        try:
            import fcntl  # type: ignore

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            return True
        except Exception:
            return False

    @staticmethod
    def _unlock_file(f):
        try:
            import fcntl  # type: ignore

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass

    def read_snapshot(self) -> Dict[str, Any]:
        state = self._default_state()
        if not self.path.exists():
            return state

        with self.path.open("r", encoding="utf-8") as f:
            locked = self._lock_file(f)
            try:
                raw = f.read().strip()
                if not raw:
                    return state
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    state.update(parsed)
                # Backfill maxima if missing
                if state.get("max_total_tokens") is None and self.max_total_tokens is not None:
                    state["max_total_tokens"] = self.max_total_tokens
                if state.get("max_chal_tokens") is None and self.max_chal_tokens is not None:
                    state["max_chal_tokens"] = self.max_chal_tokens
                return state
            finally:
                if locked:
                    self._unlock_file(f)

    def _write_state(self, state: Dict[str, Any]) -> None:
        tmp = json.dumps(state, ensure_ascii=False, indent=2)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(tmp)

    def exceeded(self, chal_id: Optional[str] = None) -> bool:
        snap = self.read_snapshot()
        used_total = int(snap.get("used", {}).get("total_tokens", 0) or 0)
        max_total = snap.get("max_total_tokens")
        if max_total is not None and used_total >= int(max_total):
            return True
        if chal_id:
            by = snap.get("by_challenge", {}).get(chal_id, {})
            used_chal = int(by.get("total_tokens", 0) or 0)
            max_chal = snap.get("max_chal_tokens")
            if max_chal is not None and used_chal >= int(max_chal):
                return True
        return False

    def consume(
        self,
        chal_id: Optional[str],
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> Dict[str, Any]:
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        total_tokens = int(total_tokens or (input_tokens + output_tokens))

        # Lock and update (best-effort; if no fcntl, this becomes per-process safe only)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as f:
            locked = self._lock_file(f)
            try:
                f.seek(0)
                raw = f.read().strip()
                state = self._default_state()
                if raw:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            state.update(parsed)
                    except Exception:
                        # If corrupted, start fresh but keep configured maxima
                        state = self._default_state()

                # Ensure maxima persisted
                if state.get("max_total_tokens") is None and self.max_total_tokens is not None:
                    state["max_total_tokens"] = self.max_total_tokens
                if state.get("max_chal_tokens") is None and self.max_chal_tokens is not None:
                    state["max_chal_tokens"] = self.max_chal_tokens

                used = state.setdefault("used", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                used["input_tokens"] = int(used.get("input_tokens", 0) or 0) + input_tokens
                used["output_tokens"] = int(used.get("output_tokens", 0) or 0) + output_tokens
                used["total_tokens"] = int(used.get("total_tokens", 0) or 0) + total_tokens

                if chal_id:
                    by = state.setdefault("by_challenge", {})
                    chal = by.setdefault(chal_id, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                    chal["input_tokens"] = int(chal.get("input_tokens", 0) or 0) + input_tokens
                    chal["output_tokens"] = int(chal.get("output_tokens", 0) or 0) + output_tokens
                    chal["total_tokens"] = int(chal.get("total_tokens", 0) or 0) + total_tokens

                state["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"

                f.seek(0)
                f.truncate(0)
                f.write(json.dumps(state, ensure_ascii=False))
                f.flush()
                try:
                    import os

                    os.fsync(f.fileno())
                except Exception:
                    pass
                return state
            finally:
                if locked:
                    self._unlock_file(f)


class InstrumentedLLM:
    """
    Wraps an LLM object (e.g., langchain_openai.ChatOpenAI) and instruments `.invoke()`.

    Important: This wrapper is designed so dynamic-loaded node `src/agent.py` does not need to
    import anything. It keeps the original `.invoke()` surface.
    """

    def __init__(
        self,
        inner_llm: Any,
        usage_logger: JSONLUsageLogger,
        base_meta: Optional[Dict[str, Any]] = None,
        budget: Optional[FileTokenBudget] = None,
        enforce_budget: bool = False,
    ):
        self._inner = inner_llm
        self._logger = usage_logger
        self._base_meta = dict(base_meta or {})
        self._local = threading.local()
        self._budget = budget
        self._enforce_budget = bool(enforce_budget)

        # Totals are per-wrapper-instance; use `with_meta()` to get per-task instances.
        self._totals = TokenTotals()

    def with_meta(self, meta: Dict[str, Any]) -> "InstrumentedLLM":
        merged = dict(self._base_meta)
        merged.update(meta or {})
        return InstrumentedLLM(
            self._inner,
            self._logger,
            merged,
            budget=self._budget,
            enforce_budget=self._enforce_budget,
        )

    @contextmanager
    def scope(self, meta: Dict[str, Any]):
        prev = getattr(self._local, "meta", None)
        merged = dict(prev or {})
        merged.update(meta or {})
        self._local.meta = merged
        try:
            yield self
        finally:
            self._local.meta = prev

    @property
    def totals(self) -> TokenTotals:
        return self._totals

    def _next_call_idx(self) -> int:
        idx = getattr(self._local, "call_idx", 0) + 1
        self._local.call_idx = idx
        return idx

    def invoke(self, *args, **kwargs):
        t0 = time.time()
        call_idx = self._next_call_idx()
        thread_meta = getattr(self._local, "meta", None) or {}
        meta = dict(self._base_meta)
        meta.update(thread_meta)
        chal_id = meta.get("chal_id")

        if self._budget and self._enforce_budget and self._budget.exceeded(chal_id=chal_id):
            self._logger.write(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                    "ts_unix": t0,
                    "latency_s": 0.0,
                    "call_idx": call_idx,
                    "ok": False,
                    "error": "TokenBudgetExceeded(precheck)",
                    **meta,
                }
            )
            raise TokenBudgetExceededError("Token budget exceeded (precheck)")

        try:
            invoke_kwargs = dict(kwargs)
            if getattr(self._inner, "accepts_dispatch_meta", False):
                existing_dispatch_meta = invoke_kwargs.get("_dispatch_meta")
                merged_dispatch_meta = dict(meta)
                if isinstance(existing_dispatch_meta, Mapping):
                    merged_dispatch_meta.update(existing_dispatch_meta)
                invoke_kwargs["_dispatch_meta"] = merged_dispatch_meta
            resp = self._inner.invoke(*args, **invoke_kwargs)
            usage = extract_token_usage(resp)
            latency_s = time.time() - t0

            inp = usage.get("input_tokens") or 0
            out = usage.get("output_tokens") or 0
            tot = usage.get("total_tokens") or (inp + out)
            self._totals = TokenTotals(
                input_tokens=self._totals.input_tokens + inp,
                output_tokens=self._totals.output_tokens + out,
                total_tokens=self._totals.total_tokens + tot,
            )

            self._logger.write(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                    "ts_unix": t0,
                    "latency_s": round(latency_s, 6),
                    "call_idx": call_idx,
                    "ok": True,
                    **meta,
                    **usage,
                }
            )

            if self._budget:
                state = self._budget.consume(chal_id=chal_id, input_tokens=inp, output_tokens=out, total_tokens=tot)
                if self._enforce_budget and self._budget.exceeded(chal_id=chal_id):
                    raise TokenBudgetExceededError("Token budget exceeded")

            return resp
        except Exception as e:
            latency_s = time.time() - t0
            self._logger.write(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                    "ts_unix": t0,
                    "latency_s": round(latency_s, 6),
                    "call_idx": call_idx,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    **meta,
                }
            )
            raise

    def __getattr__(self, name: str):
        # Proxy everything else to inner LLM (model, config, etc.)
        return getattr(self._inner, name)
