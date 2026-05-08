#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Batch test all CTF challenges listed in benchmarks/*.json
Workflow: start → test → stop for each challenge (parallelized)
Converted & enhanced from batch_ctf_test.sh
"""

import os
import sys
import json
import subprocess
import logging
import re
import signal
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional, NamedTuple
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
import argparse
# === External optional import ===
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    class tqdm:
        def __init__(self, *args, **kwargs): self.iterator = args[0]
        def __iter__(self): return iter(self.iterator)
        def update(self, n=1): pass
        def close(self): pass

# === CONFIGURATION ===
BENCHMARK_ROOT = Path(os.getenv("BENCHMARK_ROOT", Path(__file__).resolve().parents[1]))
BENCHMARK_DIR = BENCHMARK_ROOT
CHALLENGE_MANAGER_SCRIPT = BENCHMARK_ROOT / "scripts/challenge_manager.sh"

# Timeouts (seconds)
START_TIMEOUT = 600  # 5 minutes
TEST_TIMEOUT = 30    # 30 seconds
STOP_TIMEOUT = 60    # 1 minute

# Colors for terminal
COLORS = {
    "red": "\033[0;31m",
    "green": "\033[0;32m",
    "yellow": "\033[1;33m",
    "blue": "\033[0;34m",
    "nc": "\033[0m",
}

# Global control for graceful shutdown
_SHUTDOWN = threading.Event()

def signal_handler(sig, frame):
    print(f"\n{COLORS['yellow']}⚠️  Received interrupt. Stopping gracefully...{COLORS['nc']}")
    _SHUTDOWN.set()

signal.signal(signal.SIGINT, signal_handler)

# Setup logging (only for main thread)
def setup_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("ChallengeBatchTest")
    logger.setLevel(logging.DEBUG)

    # File handler (plain text)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(file_formatter)

    # Console handler (with colors, but only for main thread)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    class ColoredFormatter(logging.Formatter):
        def format(self, record):
            level_color = {
                "ERROR": COLORS["red"],
                "WARNING": COLORS["yellow"],
                "INFO": COLORS["blue"],
                "SUCCESS": COLORS["green"],
            }.get(record.levelname, "")
            reset = COLORS["nc"] if level_color else ""
            prefix = f"[{self.formatTime(record)}] "
            if record.levelname == "SUCCESS":
                msg = f"{level_color}[SUCCESS]{reset} {record.getMessage()}"
            else:
                msg = f"{level_color}[{record.levelname}]{reset} {record.getMessage()}"
            return prefix + msg

    ch.setFormatter(ColoredFormatter())
    logger.addHandler(fh)
    logger.addHandler(ch)

    # Add SUCCESS level
    logging.SUCCESS = 25
    logging.addLevelName(logging.SUCCESS, "SUCCESS")
    def success(self, message, *args, **kws):
        if self.isEnabledFor(logging.SUCCESS):
            self._log(logging.SUCCESS, message, args, **kws)
    logging.Logger.success = success

    return logger

def parse_benchmarks(benchmark_dir: Path, logger: Optional[logging.Logger] = None) -> List[Path]:
    challenges = []
    json_files = list(benchmark_dir.glob("*.json"))
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for value in data.values():
                if isinstance(value, dict) and "path" in value:
                    p = value["path"]
                    if p:
                        full_path = BENCHMARK_ROOT / p
                        if full_path.is_dir():
                            challenges.append(full_path.resolve())
        except Exception as e:
            if logger:
                logger.error(f"Failed to parse {json_file}: {e}")
    return sorted(challenges)  # deterministic order

class ChallengeResult(NamedTuple):
    name: str
    path: Path
    status: str  # "passed", "failed", "skipped"
    reason: str  # empty if passed
    log_output: str
    error_snippet: List[str]

def run_cmd(cmd: List[str], timeout: int, cwd: Optional[Path] = None) -> Tuple[bool, str]:
    """Run command, return (success, output). Safe for threads."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=os.environ.copy(),
        )
        return result.returncode == 0, result.stdout
    except subprocess.TimeoutExpired as e:
        output = (e.stdout or "") + "\n[TIMEOUT]"
        return False, output
    except Exception as e:
        return False, f"[SUBPROCESS ERROR] {e}"

def test_challenge(challenge_path: Path) -> ChallengeResult:
    """Test one challenge: start → test → stop. Thread-safe."""
    challenge_name = challenge_path.name
    dc_file = challenge_path / "docker-compose.yml"
    if not dc_file.is_file():
        return ChallengeResult(
            name=challenge_name,
            path=challenge_path,
            status="skipped",
            reason="missing docker-compose.yml",
            log_output="",
            error_snippet=[]
        )

    log_lines = []
    error_snippet = []

    # 🚦 START
    if _SHUTDOWN.is_set():
        return ChallengeResult(challenge_name, challenge_path, "skipped", "interrupted", "", [])
    start_success, start_out = run_cmd(
        [str(CHALLENGE_MANAGER_SCRIPT), "start", str(challenge_path)],
        START_TIMEOUT
    )
    log_lines.append(f"[START]\n{start_out}")
    if not start_success:
        # Try stop anyway
        run_cmd([str(CHALLENGE_MANAGER_SCRIPT), "stop", str(challenge_path)], STOP_TIMEOUT)
        return ChallengeResult(
            name=challenge_name,
            path=challenge_path,
            status="failed",
            reason="start failed/timeout",
            log_output="\n".join(log_lines),
            error_snippet=_extract_error_snippet(start_out)
        )
   

    # 🚦 STOP
    stop_success, stop_out = run_cmd(
        [str(CHALLENGE_MANAGER_SCRIPT), "stop", str(challenge_path)],
        STOP_TIMEOUT
    )
    log_lines.append(f"[STOP]\n{stop_out}")

    full_log = "\n".join(log_lines)
    if start_success :
        return ChallengeResult(challenge_name, challenge_path, "passed", "", full_log, [])
    else:
        reason = "start failed"
        return ChallengeResult(
            challenge_name,
            challenge_path,
            "failed",
            reason,
            full_log,
            _extract_error_snippet(full_log)
        )

def _extract_error_snippet(text: str, max_lines: int = 10) -> List[str]:
    pattern = re.compile(r"(ERROR|FAILED|timeout|exception|Traceback)", re.IGNORECASE)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and pattern.search(stripped):
            lines.append("  " + stripped)
            if len(lines) >= max_lines:
                break
    return lines

def main():
    parser = argparse.ArgumentParser(description="Batch test CTF challenges in parallel.")
    parser.add_argument("-j", "--jobs", type=int, default=4,
                        help="Number of parallel jobs (default: 4)")
    args = parser.parse_args()

    # Setup logs
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(f"batch_ctf_test_{timestamp}.log")
    summary_file = Path(f"batch_ctf_summary_{timestamp}.txt")

    logger = setup_logger(log_file)
    logger.info("Starting batch CTF test (parallel)")
    logger.info(f"Benchmark root: {BENCHMARK_ROOT}")
    logger.info(f"CTF manager: {CHALLENGE_MANAGER_SCRIPT}")
    logger.info(f"Log file: {log_file}")
    logger.info(f"Concurrency: {args.jobs}")

    # Validate
    if not BENCHMARK_DIR.is_dir():
        logger.error(f"benchmark directory not found: {BENCHMARK_DIR}")
        sys.exit(1)
    if not CHALLENGE_MANAGER_SCRIPT.is_file() or not os.access(CHALLENGE_MANAGER_SCRIPT, os.X_OK):
        logger.error(f"challenge_manager.sh not executable: {CHALLENGE_MANAGER_SCRIPT}")
        sys.exit(1)

    # Parse & pre-filter valid challenges
    all_challenges = parse_benchmarks(BENCHMARK_DIR, logger)
    if not all_challenges:
        logger.error("No valid challenges found!")
        sys.exit(1)

    logger.info(f"Total challenges: {len(all_challenges)}")
    filtered_challenges = [
        p for p in all_challenges
        if (p / "docker-compose.yml").is_file()
    ]
    skipped_pre = len(all_challenges) - len(filtered_challenges)
    if skipped_pre:
        logger.warning(f"{skipped_pre} challenges skipped (no docker-compose.yml)")

    # 🔁 Parallel execution
    results: List[ChallengeResult] = []
    futures: List[Future] = []

    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            # Submit all
            future_to_path = {
                executor.submit(test_challenge, path): path
                for path in filtered_challenges
            }

            # Collect with progress bar
            progress_iter = as_completed(future_to_path)
            if HAS_TQDM:
                progress_iter = tqdm(
                    progress_iter,
                    total=len(future_to_path),
                    desc="Testing challenges",
                    unit="chal"
                )

            for future in progress_iter:
                if _SHUTDOWN.is_set():
                    # Cancel remaining
                    for f in future_to_path:
                        f.cancel()
                    break
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    path = future_to_path[future]
                    results.append(ChallengeResult(
                        name=path.name,
                        path=path,
                        status="failed",
                        reason=f"exception in worker: {e}",
                        log_output="",
                        error_snippet=[f"  EXCEPTION: {e}"]
                    ))

    except KeyboardInterrupt:
        print(f"\n{COLORS['yellow']}⚠️  Interrupted. Waiting for ongoing tasks...{COLORS['nc']}")
        _SHUTDOWN.set()
        # Let finally handle cleanup

    # Final results accounting for pre-skipped
    passed = [r for r in results if r.status == "passed"]
    failed = [r for r in results if r.status == "failed"]
    skipped = [
        ChallengeResult(p.name, p, "skipped", "missing docker-compose.yml", "", [])
        for p in all_challenges if (p / "docker-compose.yml").is_file() == False
    ]
    if _SHUTDOWN.is_set():
        interrupted = [r for r in results if r.status == "skipped" and r.reason == "interrupted"]
        skipped.extend(interrupted)

    # Write logs (thread-safe: do in main thread)
    with open(log_file, "a", encoding="utf-8") as f:
        for r in results:
            if r.log_output:
                f.write(f"\n=== {r.name} ===\n")
                f.write(r.log_output)
                f.write("\n")

    # Summary
    summary_lines = [
        "================== BATCH CTF TEST SUMMARY ==================",
        f"Time: {datetime.now()}",
        f"Benchmark root: {BENCHMARK_ROOT}",
        f"Total challenges: {len(all_challenges)}",
        f"✅ Passed: {len(passed)}",
        f"❌ Failed: {len(failed)}",
        f"⏭️  Skipped: {len(skipped)}",
        "",
    ]

    if failed:
        summary_lines.append("Failed challenges:")
        for r in failed:
            summary_lines.append(f"  • {r.name} ({r.reason})")
        summary_lines.append("")

    if skipped:
        summary_lines.append("Skipped challenges:")
        for r in skipped:
            summary_lines.append(f"  • {r.name} ({r.reason})")
        summary_lines.append("")

    if not failed and not skipped:
        summary_lines.append("🎉 All challenges passed!")
    summary_lines.append(f"🔍 Full log: {log_file}")

    summary_text = "\n".join(summary_lines)
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(summary_text)

    # Print to console
    print("\n" + summary_text)

    # Print error snippets for failures
    for r in failed:
        if r.error_snippet:
            print(f"\n{COLORS['red']}--- ERROR SNIPPET: {r.name} ---{COLORS['nc']}")
            print("\n".join(r.error_snippet[:10]))
            print(f"{COLORS['red']}{'-'*50}{COLORS['nc']}")

    # Exit code
    if failed:
        print(f"\n{COLORS['red']}❌ {len(failed)} challenge(s) failed.{COLORS['nc']}")
        sys.exit(1)
    else:
        msg = f"✅ All {len(passed)} challenge(s) passed!"
        if skipped:
            msg += f" ({len(skipped)} skipped)"
        print(f"\n{COLORS['green']}{msg}{COLORS['nc']}")
        sys.exit(0)

if __name__ == "__main__":
    main()
