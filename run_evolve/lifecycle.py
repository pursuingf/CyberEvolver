"""Process lifecycle, signal handlers, and logging setup for evolution runs."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional

import psutil


def finish_challenge_with_logging(
    *,
    challenge_client: Any,
    chal_id: str,
    logger: logging.Logger,
    pid: Optional[int] = None,
) -> bool:
    if challenge_client is None or not chal_id:
        return False
    prefix = f"[PID:{pid}] " if pid is not None else ""
    try:
        challenge_client.finish_challenge(chal_id)
        logger.info("🧹 %sTeardown target done: %s", prefix, chal_id)
        return True
    except Exception as teardown_error:
        logger.warning("⚠️ %sTeardown target failed: %s | %s", prefix, chal_id, teardown_error)
        return False


def cleanup_inflight_challenges(
    *,
    challenge_client: Any,
    inflight_futures: MutableMapping[Any, Dict[str, Any]],
    global_logger: logging.Logger,
) -> List[str]:
    cleaned: List[str] = []
    seen: set[str] = set()
    for context in inflight_futures.values():
        chal_id = str(context.get("chal_id", "") or "").strip()
        if not chal_id or chal_id in seen:
            continue
        finish_challenge_with_logging(
            challenge_client=challenge_client,
            chal_id=chal_id,
            logger=global_logger,
        )
        seen.add(chal_id)
        cleaned.append(chal_id)
    return cleaned


def cleanup_on_interrupt(
    *,
    executor: Any,
    challenge_client: Any,
    inflight_futures: MutableMapping[Any, Dict[str, Any]],
    global_logger: logging.Logger,
) -> List[str]:
    if executor:
        executor.shutdown(wait=False, cancel_futures=True)
    kill_all_descendants(logger=global_logger)
    return cleanup_inflight_challenges(
        challenge_client=challenge_client,
        inflight_futures=inflight_futures,
        global_logger=global_logger,
    )


def kill_all_descendants(logger=None):
    current_pid = os.getpid()
    try:
        parent = psutil.Process(current_pid)
    except psutil.NoSuchProcess:
        return

    children = parent.children(recursive=True)

    if children:
        targets = []
        for proc in children:
            try:
                cmdline = proc.cmdline()
                cmd_str = " ".join(cmdline)

                if "multiprocessing.resource_tracker" in cmd_str:
                    continue

                targets.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not targets:
            return

        msg = f"🔪 Killing {len(targets)} descendant processes..."
        if logger:
            logger.warning(msg)
        else:
            print(msg)

        for process in targets:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass

        _, alive = psutil.wait_procs(targets, timeout=360)

        if alive:
            if logger:
                logger.warning(f"💀 Force killing {len(alive)} stubborn processes...")
            for process in alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass


def signal_handler(signum, frame):
    print(f"\n🛑 Received signal {signum}. Cleaning up...")
    kill_all_descendants()
    sys.exit(signum)


def sigterm_handler(signum, frame):
    raise SystemExit("Received SIGTERM, shutting down gracefully.")


def setup_logger(
    name: str,
    log_file: Path,
    level: int = logging.INFO,
    console: bool = False,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
        logger.addHandler(ch)

    return logger


def get_challenge_logger(chal_id: str, chal_category: str, run_dir: Path) -> logging.Logger:
    chal_dir = run_dir / chal_category / chal_id
    chal_dir.mkdir(parents=True, exist_ok=True)
    log_file = chal_dir / "evolution.log"
    return setup_logger(f"challenge.{chal_id}", log_file, console=False)


def setup_run_directory(args) -> Path:
    """Set up the evolution run directory with metadata."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if args.challenge_id:
        run_dir = Path("logs/evolution_data") / args.challenge_id / f"{timestamp}_{args.run_id}"
    else:
        run_dir = Path("logs/evolution_data") / args.model / f"{timestamp}_{args.run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
