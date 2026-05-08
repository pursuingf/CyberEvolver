# --- persistent_isolated_worker.py ---
import io
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
from typing import Any, Dict, Optional, Tuple

# Strongly recommended for CUDA safety with multiprocessing on Linux
try:
    mp.set_start_method("spawn", force=True)
except RuntimeError:
    pass  # already set

STOP_MSG = ("__STOP__",)  # pickleable sentinel

def _worker_main(task_q: mp.Queue, result_q: mp.Queue):
    """
    Persistent worker: receives (task_id, func_path, args, extra_env_dict) and returns
    (task_id, payload_dict). Always returns a structured payload (never raises).
    """
    import os as _os
    import sys as _sys
    import importlib as _importlib

    def _apply_env(delta: Optional[Dict[str, str]]) -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[str]]]:
        if not delta:
            return {}, {}
        originals = {}
        added = {}
        for k, v in delta.items():
            if k in _os.environ:
                originals[k] = _os.environ[k]
            else:
                added[k] = None
            _os.environ[k] = v
        return originals, added

    def _restore_env(originals: Dict[str, Optional[str]], added: Dict[str, Optional[str]]):
        for k, v in originals.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        for k in added.keys():
            _os.environ.pop(k, None)

    while True:
        task = task_q.get()
        if task == STOP_MSG:
            break

        # Expect (task_id, func_path, args, extra_env)
        try:
            task_id, func_path, args, extra_env = task
        except Exception:
            # Malformed task; return structured failure and keep serving
            try:
                result_q.put((None, {
                    "ok": False, "result": None, "error": "Malformed task",
                    "stdout": "", "stderr": "", "traceback": ""
                }))
            except Exception:
                pass
            continue

        # Capture stdout/stderr per-task
        _stdout_buf, _stderr_buf = io.StringIO(), io.StringIO()
        _old_stdout, _old_stderr = _sys.stdout, _sys.stderr
        _sys.stdout, _sys.stderr = _stdout_buf, _stderr_buf

        originals = {}
        added = {}
        payload: Dict[str, Any] = {}
        try:
            try:
                originals, added = _apply_env(extra_env)
                mod_name, fn_name = func_path.split(":")
                mod = _importlib.import_module(mod_name)
                # Optional: reload to avoid sticky state across calls
                mod = _importlib.reload(mod)
                fn = getattr(mod, fn_name)

                result = fn(*args)

                payload = {"ok": True, "result": result, "error": None}
            except Exception as e:
                tb = traceback.format_exc()
                payload = {"ok": False, "result": None, "error": f"{e}", "traceback": tb}
        finally:
            _restore_env(originals, added)
            payload.setdefault("traceback", "")
            payload["stdout"] = _stdout_buf.getvalue()
            payload["stderr"] = _stderr_buf.getvalue()
            _sys.stdout, _sys.stderr = _old_stdout, _old_stderr

        try:
            result_q.put((task_id, payload))
        except Exception:
            pass


class PersistentIsolatedWorker:
    """
    Reuses a single subprocess to run arbitrary `module:function` calls.
    If a call times out or the process dies (segfault/OOM), we kill & restart it,
    and return a structured error for that call.
    """
    def __init__(self):
        self._task_q: mp.Queue = mp.Queue(maxsize=1)  # 1 at a time by design
        self._result_q: mp.Queue = mp.Queue(maxsize=1)
        self._proc: Optional[mp.Process] = None
        self._next_task_id = 0
        self._start_proc()

    def _start_proc(self):
        self._proc = mp.Process(target=_worker_main, args=(self._task_q, self._result_q), daemon=True)
        self._proc.start()

    def _ensure_alive(self):
        if self._proc is None or not self._proc.is_alive():
            self._start_proc()

    def stop(self):
        try:
            if self._proc and self._proc.is_alive():
                try:
                    self._task_q.put(STOP_MSG, timeout=0.2)
                except Exception:
                    pass
                self._proc.join(timeout=1.0)
        except Exception:
            pass
        finally:
            if self._proc and self._proc.is_alive():
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def _hard_restart(self):
        try:
            if self._proc and self._proc.is_alive():
                self._proc.kill()
        except Exception:
            pass
        self._proc = None
        # Drain queues to avoid stale messages
        try:
            while True:
                self._result_q.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                self._task_q.get_nowait()
        except queue.Empty:
            pass
        self._start_proc()

    def call(self, func_path: str, args: list, timeout_s: int, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Returns schema: { ok, returncode, result, stdout, stderr, error }
        - returncode 0 if structured return from child
        - -9 on timeout
        - >0 if child crashed (exitcode)
        """
        self._ensure_alive()
        task_id = self._next_task_id
        self._next_task_id += 1

        try:
            self._task_q.put((task_id, func_path, args, extra_env or {}), timeout=0.5)
        except queue.Full:
            self._hard_restart()
            return {
                "ok": False, "returncode": 1, "result": None,
                "stdout": "", "stderr": "", "error": "Internal queue full; worker restarted.",
            }

        deadline = time.time() + float(timeout_s)
        while True:
            # Crash detection
            if self._proc is None or not self._proc.is_alive():
                rc = self._proc.exitcode if self._proc is not None else 1
                self._hard_restart()
                return {
                    "ok": False, "returncode": rc if rc is not None else 1,
                    "result": None, "stdout": "", "stderr": "",
                    "error": f"Subprocess crashed (rc={rc}). Likely SIGSEGV/OOM. Check stderr; enable compute-sanitizer for details.",
                }

            remaining = deadline - time.time()
            if remaining <= 0:
                self._hard_restart()
                return {
                    "ok": False, "returncode": -9, "result": None,
                    "stdout": "", "stderr": "", "error": f"Timeout after {timeout_s}s",
                }

            try:
                got_id, payload = self._result_q.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue

            if got_id != task_id:
                # Unexpected; drop and keep waiting
                continue

            payload.setdefault("stdout", "")
            payload.setdefault("stderr", "")
            payload.setdefault("error", None)
            payload["returncode"] = 0
            return payload