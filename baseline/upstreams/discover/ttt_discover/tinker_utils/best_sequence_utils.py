from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from ttt_discover.tinker_utils.state import to_json_serializable


# -----------------------------
# Simple cross-process file lock
# -----------------------------
@contextmanager
def _file_lock(lock_path: str, *, poll_s: float = 0.05, stale_s: float = 600.0):
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode("utf-8"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            # If stale, delete and try again
            try:
                st = os.stat(lock_path)
                if (time.time() - st.st_mtime) > stale_s:
                    os.remove(lock_path)
                    continue
            except FileNotFoundError:
                continue
            time.sleep(poll_s)

    try:
        yield
    finally:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


def _atomic_write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(to_json_serializable(obj), f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _read_json_or_default(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Corrupt/partial file; treat as empty
        return default


# TODO: Probably not necessary for future
def _get_best_from_prior_step(store: Dict[str, Any], step: int) -> Tuple[Optional[List[float]], Optional[float]]:
    if step < 0:
        return None, None

    # keys are stringified ints
    candidates = []
    for k in store.keys():
        try:
            candidates.append(int(k))
        except Exception:
            continue
    candidates = [s for s in candidates if s <= step]
    if not candidates:
        return None, None

    best_step = max(candidates)
    entry = store.get(str(best_step), {})
    sol = entry.get("best_solution", None)
    bnd = entry.get("bound", None)
    return sol, bnd


# TODO: Will be out of date after updating to tree
def try_save_best_sequence(result: list, bound: float, path: str, step: int, is_maximize: bool = False) -> bool:
    lock_path = f"{path}.lock"

    with _file_lock(lock_path):
        store: Dict[str, Any] = _read_json_or_default(path, default={})
        step_key = str(step)

        wrote_new_best = False

        # 1) If missing, copy forward best from previous step
        if step_key not in store:
            if step == 0:
                baseline_solution, baseline_bound = None, None
            else:
                baseline_solution, baseline_bound = _get_best_from_prior_step(store, step - 1)

            if baseline_bound is not None:
                store[step_key] = {
                    "step": int(step),
                    "best_solution": baseline_solution,
                    "bound": float(baseline_bound),
                }
            else:
                # Explicitly initialize empty step
                store[step_key] = {
                    "step": int(step),
                    "best_solution": None,
                    "bound": None,
                }

        # 2) Compare against current step baseline
        baseline_bound = store[step_key]["bound"]

        if is_maximize:
            should_update = baseline_bound is None or bound > float(baseline_bound)
        else:
            should_update = baseline_bound is None or bound < float(baseline_bound)

        if should_update:
            store[step_key] = {
                "step": int(step),
                "best_solution": result,
                "bound": float(bound),
            }
            wrote_new_best = True

        # 3) Persist exactly once
        _atomic_write_json(path, store)

        return wrote_new_best


# TODO: Modify saving here to have tree structure rather than only saving best



# TODO: Function might be out of date after updating, can probably remove
def get_best_sequence(path: str, step: int) -> Tuple[Optional[List[float]], Optional[float]]:
    lock_path = f"{path}.lock"

    with _file_lock(lock_path):
        store: Dict[str, Any] = _read_json_or_default(path, default={})
        return _get_best_from_prior_step(store, step - 1)

# TODO: Add function to sample from tree


def clear_step_entry(path: str, step: int) -> bool:
    lock_path = f"{path}.lock"
    step_key = str(step)

    with _file_lock(lock_path):
        store: Dict[str, Any] = _read_json_or_default(path, default={})

        if step_key not in store:
            return False

        del store[step_key]
        _atomic_write_json(path, store)
        return True


def get_best_bound_path(tinker_log_path: str) -> str:
    return os.path.join(tinker_log_path, "best_sequences.json")
