#!/usr/bin/env python3
"""
Baseline Task Scheduler — drives Codex / Claude Code sessions in parallel
to complete baseline reproduction tasks.

Dispatch model:
  - Tasks whose deps are all DONE form a "wave"
  - All tasks in a wave are dispatched in parallel (up to --max-parallel)
  - After the wave completes, reconcile DAG and form the next wave
  - Repeat until no tasks remain or budget exhausted

Usage:
  python baseline/scripts/task_scheduler.py [--dry-run] [--max-sessions 30]
  [--max-parallel 6] [--worktree-id ID]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # evolve_ctf_agent/
BASELINE_ROOT = REPO_ROOT / "baseline"
RESEARCH_CTX = BASELINE_ROOT / "research_context"
TASKS_DIR = RESEARCH_CTX / "tasks"
DAG_FILE = RESEARCH_CTX / "dag.md"
INSTRUCTION_FILE = RESEARCH_CTX / "instruction.md"

WORKTREE_BRANCH = "agent/baseline-repro"
DEFAULT_WORKTREE_ID = "baseline-worktree"
WORKTREE_ROOT = REPO_ROOT / ".worktree"

CODEX_BIN = "codex"
CLAUDE_BIN = "claude"
CODEX_SWITCHER_BIN = "codex-switcher"
SESSION_TIMEOUT_SECONDS = 3600
CODEX_QUOTA_POLL_INTERVAL_SECONDS = 300

CODEX_RATE_LIMIT_PATTERNS = [
    "you've hit your usage limit",
    "insufficient_quota",
    "too many requests",
]

CLAUDE_RATE_LIMIT_PATTERNS = [
    "you've hit your limit",
    "overloaded",
]

LOG_BASE = RESEARCH_CTX / "scheduler_logs"
LOG_DIR: Path = LOG_BASE  # set in main() per run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("baseline-scheduler")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

AGENT_CODEX = "codex"
AGENT_CLAUDE = "claude"
AGENT_AUTO = "auto"


@dataclass
class TaskCard:
    task_id: str
    filename: str
    filepath: Path
    status: str
    deps: list[str] = field(default_factory=list)
    agent: str = AGENT_AUTO


@dataclass
class TaskResult:
    task: TaskCard
    session_id: int
    agent_type: str
    success: bool
    output: str
    elapsed: float
    rate_limited: bool


# ---------------------------------------------------------------------------
# Locks for thread safety
# ---------------------------------------------------------------------------

_git_lock = threading.Lock()
_codex_switch_lock = threading.Lock()
_session_id_lock = threading.Lock()
_session_counter = 0


def next_session_id() -> int:
    global _session_counter
    with _session_id_lock:
        _session_counter += 1
        return _session_counter


def set_session_counter(value: int) -> None:
    global _session_counter
    _session_counter = value


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_dag_tasks(dag_path: Path) -> dict[str, dict]:
    """Parse the task status table from dag.md."""
    content = dag_path.read_text(encoding="utf-8")
    tasks: dict[str, dict] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.split("|")]
        cols = [c for c in cols if c]
        if len(cols) < 6:
            continue
        tid = cols[0]
        if not re.match(r"t\d+", tid):
            continue
        status_raw = cols[3]
        deps_raw = cols[4]
        agent_raw = cols[5].lower().strip() if len(cols) > 5 else "auto"

        if "✅" in status_raw:
            status = "DONE"
        elif "🔄" in status_raw:
            status = "IN_PROGRESS"
        else:
            status = "TODO"

        deps = []
        if deps_raw and deps_raw != "无":
            deps = [d.strip() for d in re.findall(r"t\d+", deps_raw)]

        agent = AGENT_AUTO
        if "claude" in agent_raw:
            agent = AGENT_CLAUDE
        elif "codex" in agent_raw:
            agent = AGENT_CODEX

        tasks[tid] = {"status": status, "deps": deps, "agent": agent}
    return tasks


def find_dispatchable_tasks(dag_path: Path, tasks_dir: Path) -> list[TaskCard]:
    """Return all tasks whose deps are satisfied and status != DONE."""
    dag_tasks = parse_dag_tasks(dag_path)
    done_ids = {tid for tid, info in dag_tasks.items() if info["status"] == "DONE"}

    dispatchable = []
    for task_file in sorted(tasks_dir.glob("t*.md")):
        tid = task_file.stem.split("_")[0]
        if tid not in dag_tasks:
            log.warning("Task file %s not found in dag.md, skipping", task_file.name)
            continue
        info = dag_tasks[tid]
        if info["status"] == "DONE":
            continue
        if all(d in done_ids for d in info["deps"]):
            dispatchable.append(
                TaskCard(
                    task_id=tid,
                    filename=task_file.name,
                    filepath=task_file,
                    status=info["status"],
                    deps=info["deps"],
                    agent=info["agent"],
                )
            )
    return dispatchable


def tasks_remaining(tasks_dir: Path) -> list[Path]:
    if not tasks_dir.exists() or not tasks_dir.is_dir():
        raise RuntimeError(f"Expected tasks directory at {tasks_dir}")
    return sorted(tasks_dir.glob("t*.md"))


def primary_task_archive_dir(tasks_dir: Path) -> Path:
    return tasks_dir / "archive"


def candidate_task_archive_dirs(tasks_dir: Path) -> list[Path]:
    primary = primary_task_archive_dir(tasks_dir)
    legacy = tasks_dir.parent / "archive"
    dirs = [primary]
    if legacy != primary:
        dirs.append(legacy)
    return dirs


def task_card_id(task_path: Path) -> str:
    return task_path.stem.split("_")[0]


def find_archived_task_card(tasks_dir: Path, filename: str) -> Path | None:
    for archive_dir in candidate_task_archive_dirs(tasks_dir):
        archived = archive_dir / filename
        if archived.exists():
            return archived
    return None


def rewrite_task_card_status(task_path: Path, status_text: str) -> None:
    content = task_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"^## Status:.*$",
        f"## Status: {status_text}",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        updated = f"## Status: {status_text}\n\n{content}"
    task_path.write_text(updated, encoding="utf-8")


def reconcile_task_cards(dag_path: Path, tasks_dir: Path) -> None:
    """Keep live task cards aligned with dag.md task state."""
    dag_tasks = parse_dag_tasks(dag_path)
    archive_dir = primary_task_archive_dir(tasks_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)

    live_task_cards = {
        task_card_id(p): p for p in sorted(tasks_dir.glob("t*.md"))
    }
    archived_task_cards: dict[str, list[Path]] = {}
    for cand_dir in candidate_task_archive_dirs(tasks_dir):
        if not cand_dir.is_dir():
            continue
        for p in sorted(cand_dir.glob("t*.md")):
            archived_task_cards.setdefault(task_card_id(p), []).append(p)

    for task_id, info in dag_tasks.items():
        live = live_task_cards.get(task_id)
        archived = archived_task_cards.get(task_id, [])

        if info["status"] == "DONE":
            if live is None:
                continue
            live.replace(archive_dir / live.name)
            continue

        if live is not None or not archived:
            continue

        restored = tasks_dir / archived[0].name
        archived[0].replace(restored)
        rewrite_task_card_status(restored, "🔲 TODO")


# ---------------------------------------------------------------------------
# Worktree management
# ---------------------------------------------------------------------------


def normalize_worktree_id(worktree_id: str) -> str:
    worktree_id = worktree_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", worktree_id):
        raise ValueError("worktree_id must match [A-Za-z0-9][A-Za-z0-9._-]*")
    return worktree_id


def resolve_worktree_dir(worktree_id: str) -> Path:
    return WORKTREE_ROOT / normalize_worktree_id(worktree_id)


def resolve_worktree_branch(branch: str, worktree_dir: Path) -> str:
    default_dir = WORKTREE_ROOT / DEFAULT_WORKTREE_ID
    if worktree_dir.resolve() == default_dir.resolve():
        return branch
    suffix = re.sub(r"[^a-z0-9]+", "-", worktree_dir.name.lower()).strip("-")
    if not suffix:
        suffix = "worktree"
    return f"{branch}-{suffix}"


def resolve_worktree_head(worktree_dir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Existing worktree at {worktree_dir} is not a valid git worktree"
        ) from exc
    head = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError(f"Unable to resolve HEAD for worktree {worktree_dir}")
    return head


def normalize_sparse_paths(sparse_paths: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw_path in sparse_paths or []:
        sparse_path = raw_path.strip().strip("/")
        if not sparse_path:
            raise ValueError("sparse checkout paths must not be empty")
        if sparse_path == ".":
            raise ValueError("sparse checkout paths must be repo-relative")
        if sparse_path.startswith("../") or "/../" in sparse_path or sparse_path == "..":
            raise ValueError("sparse checkout paths must not traverse outside the repo")
        normalized.append(sparse_path)
    return normalized


def apply_sparse_checkout(worktree_dir: Path, sparse_paths: list[str] | None) -> None:
    sparse_paths = normalize_sparse_paths(sparse_paths)
    if not sparse_paths:
        return

    subprocess.run(
        ["git", "-C", str(worktree_dir), "sparse-checkout", "init", "--cone"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree_dir), "sparse-checkout", "set", *sparse_paths],
        check=True,
    )
    log.info(
        "Configured sparse checkout for %s: %s",
        worktree_dir,
        ", ".join(sparse_paths),
    )


def validate_execution_workspace(workspace_dir: Path) -> None:
    tasks_dir = workspace_dir / "baseline" / "research_context" / "tasks"
    dag_file = workspace_dir / "baseline" / "research_context" / "dag.md"
    if not tasks_dir.is_dir() or not dag_file.is_file():
        raise RuntimeError(
            f"Workspace at {workspace_dir} does not contain baseline task inputs"
        )


def setup_worktree(
    repo_root: Path,
    worktree_dir: Path,
    branch: str,
    start_point_worktree_dir: Path | None = None,
) -> Path:
    branch = resolve_worktree_branch(branch, worktree_dir)

    if worktree_dir.exists():
        git_link = worktree_dir / ".git"
        tasks_dir = worktree_dir / "baseline" / "research_context" / "tasks"
        dag_file = worktree_dir / "baseline" / "research_context" / "dag.md"
        if not git_link.exists() or not tasks_dir.is_dir() or not dag_file.is_file():
            raise RuntimeError(
                f"Existing worktree at {worktree_dir} is not a valid baseline worktree"
            )
        log.info("Worktree already exists at %s", worktree_dir)
        return worktree_dir

    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    start_point_head: str | None = None
    if start_point_worktree_dir is not None:
        start_point_head = resolve_worktree_head(start_point_worktree_dir)
        log.info("Using start point worktree %s at %s", start_point_worktree_dir, start_point_head)

    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo_root, capture_output=True, text=True,
    )
    if not result.stdout.strip():
        branch_cmd = ["git", "branch", branch]
        if start_point_head is not None:
            branch_cmd.append(start_point_head)
        subprocess.run(branch_cmd, cwd=repo_root, check=True)
        log.info("Created branch %s", branch)
    elif start_point_head is not None:
        log.info("Branch %s already exists; start point ignored", branch)

    subprocess.run(
        ["git", "worktree", "add", str(worktree_dir), branch],
        cwd=repo_root, check=True,
    )
    log.info("Created worktree at %s on branch %s", worktree_dir, branch)
    return worktree_dir


def prepare_workspace(args: argparse.Namespace) -> Path:
    if args.use_current_workspace:
        if args.start_point_worktree_id is not None:
            raise ValueError("--start-point-worktree-id requires worktree mode")
        if args.sparse_path:
            raise ValueError("--sparse-path requires worktree mode")
        validate_execution_workspace(REPO_ROOT)
        return REPO_ROOT

    worktree_dir = resolve_worktree_dir(args.worktree_id)
    start_point_worktree_dir = None
    if args.start_point_worktree_id is not None:
        start_point_worktree_dir = resolve_worktree_dir(args.start_point_worktree_id)

    setup_worktree(
        REPO_ROOT,
        worktree_dir,
        WORKTREE_BRANCH,
        start_point_worktree_dir=start_point_worktree_dir,
    )
    apply_sparse_checkout(worktree_dir, args.sparse_path)
    validate_execution_workspace(worktree_dir)
    return worktree_dir


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


@dataclass
class CodexQuota:
    available: bool
    email: str = ""
    hourly_pct: int = 0
    hourly_reset_at: int = 0


def get_codex_quota() -> CodexQuota:
    try:
        result = subprocess.run(
            [CODEX_SWITCHER_BIN, "--best", "--json"],
            capture_output=True, text=True, timeout=30,
            env=build_proxy_env(),
        )
        best = json.loads(result.stdout)
        email = best.get("email", "")
        hourly_pct = best.get("hourly_remaining_pct", 0)
        hourly_reset_at = best.get("hourly_reset_at", 0)

        if hourly_pct > 10:
            return CodexQuota(available=True, email=email, hourly_pct=hourly_pct, hourly_reset_at=hourly_reset_at)
        return CodexQuota(available=False, email=email, hourly_pct=hourly_pct, hourly_reset_at=hourly_reset_at)

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError) as e:
        log.error("Codex account check failed: %s", e)
        return CodexQuota(available=False)


def sleep_until_codex_reset(quota: CodexQuota) -> None:
    wait = CODEX_QUOTA_POLL_INTERVAL_SECONDS
    log.info(
        "Codex quota exhausted (%s, %d%%). Sleeping %dm%ds before polling again.",
        quota.email, quota.hourly_pct, wait // 60, wait % 60,
    )
    time.sleep(wait)


def switch_codex_to_best_once() -> None:
    """Thread-safe: only the first caller actually switches."""
    with _codex_switch_lock:
        log.info("Switching codex to best available account")
        try:
            subprocess.run(
                [CODEX_SWITCHER_BIN, "--switch", "best"],
                check=True, timeout=30,
                env=build_proxy_env(),
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.error("Codex account switch failed: %s", e)


def normalized_nonempty_lines(output: str) -> list[str]:
    return [line.strip().lower() for line in output.splitlines() if line.strip()]


def is_codex_rate_limited(output: str) -> bool:
    tail_lines = normalized_nonempty_lines(output)[-20:]
    if not tail_lines:
        return False
    tail_text = "\n".join(tail_lines)
    if any(pattern in tail_text for pattern in CODEX_RATE_LIMIT_PATTERNS):
        return True
    return (
        "http error: 403 forbidden" in tail_text
        and (
            "backend-api/codex/responses" in tail_text
            or "codex_api::endpoint::responses_websocket" in tail_text
        )
    )


def is_claude_rate_limited(output: str) -> bool:
    head_lines = normalized_nonempty_lines(output)[:5]
    if not head_lines:
        return False
    return any(
        line.startswith(pattern)
        for line in head_lines
        for pattern in CLAUDE_RATE_LIMIT_PATTERNS
    )


def is_rate_limited(output: str, agent_type: str = AGENT_CODEX) -> bool:
    if agent_type == AGENT_CLAUDE:
        return is_claude_rate_limited(output)
    return is_codex_rate_limited(output)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def build_proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    clash_host = env.get("CLASH_HOST", "0.0.0.0")
    clash_port = env.get("CLASH_MIXED_PORT", "7890")
    http_proxy = f"http://{clash_host}:{clash_port}"
    socks_proxy = f"socks5://{clash_host}:{clash_port}"
    no_proxy = "localhost,127.0.0.1,::1"
    env.update({
        "http_proxy": http_proxy, "https_proxy": http_proxy,
        "all_proxy": socks_proxy, "HTTP_PROXY": http_proxy,
        "HTTPS_PROXY": http_proxy, "ALL_PROXY": socks_proxy,
        "no_proxy": no_proxy, "NO_PROXY": no_proxy,
    })
    return env


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def build_task_prompt(task: TaskCard, worktree_dir: Path) -> str:
    baseline_ctx = worktree_dir / "baseline" / "research_context"
    instruction = (baseline_ctx / "instruction.md").read_text(encoding="utf-8")
    dag = (baseline_ctx / "dag.md").read_text(encoding="utf-8")
    task_content = (baseline_ctx / "tasks" / task.filename).read_text(encoding="utf-8")

    readme_path = worktree_dir / "baseline" / "readme.md"
    readme_context = ""
    if readme_path.exists():
        readme_context = readme_path.read_text(encoding="utf-8")

    return f"""You are a research assistant working on reproducing cybersecurity agent baselines.
You are executing a specific task card. Follow the instructions precisely.

IMPORTANT: You are running in non-interactive mode.
Do not ask questions or wait for clarification. Make reasonable decisions.

WORKING DIRECTORY: {worktree_dir}

=== AGENT INSTRUCTIONS ===
{instruction}

=== CURRENT DAG ===
{dag}

=== YOUR TASK ===
{task_content}

=== BASELINE README (ChallengeClient integration reference) ===
{readme_context}

=== EXECUTION RULES ===
1. Complete ONLY what is specified in the task card's Scope section.
2. Write outputs to the paths specified in the Output section.
3. When done, update the task card: set Status to "DONE", fill Completion Notes.
4. Update dag.md: change your task ({task.task_id}) status from 🔲 to ✅
5. If you hit an unresolvable issue, document in Issues section and STOP.
6. Do NOT modify files outside your task's scope.
7. Use English for code comments.
8. For git clone operations, use --depth 1 for shallow clones.
9. For pip install, always use a venv to avoid polluting the global environment.
"""


# ---------------------------------------------------------------------------
# Run agent sessions
# ---------------------------------------------------------------------------


def coerce_subprocess_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def write_session_output_log(session_id: int, label: str, output: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / f"session_{session_id:03d}_{label}_output.log").write_text(
        output, encoding="utf-8"
    )


def write_session_prompt_log(session_id: int, label: str, prompt: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / f"session_{session_id:03d}_{label}_prompt.md").write_text(
        prompt, encoding="utf-8"
    )


def format_timeout_output(exc: subprocess.TimeoutExpired) -> str:
    timeout = int(exc.timeout) if exc.timeout is not None else SESSION_TIMEOUT_SECONDS
    stdout = coerce_subprocess_output(getattr(exc, "output", None))
    stderr = coerce_subprocess_output(getattr(exc, "stderr", None))

    parts = [f"[TIMEOUT after {timeout}s]"]
    if stdout:
        parts.extend(["", "=== STDOUT ===", stdout.rstrip("\n")])
    if stderr:
        parts.extend(["", "=== STDERR ===", stderr.rstrip("\n")])
    return "\n".join(parts).rstrip() + "\n"


def run_codex_session(
    prompt: str,
    worktree_dir: Path,
    session_id: int,
    label: str,
    dry_run: bool = False,
) -> tuple[bool, str, float]:
    log.info("[codex] Session %d: %s — starting", session_id, label)
    if dry_run:
        log.info("[DRY RUN] codex prompt (%d chars)", len(prompt))
        return True, "[dry run]", 0.0

    # Re-evaluate the best available Codex account at actual invocation time,
    # not only during quota refresh, so fallback paths can recover cleanly.
    switch_codex_to_best_once()
    write_session_prompt_log(session_id, label, prompt)
    started = time.monotonic()

    try:
        result = subprocess.run(
            [CODEX_BIN, "exec", "--full-auto", "-C", str(worktree_dir)],
            input=prompt, capture_output=True, text=True,
            timeout=SESSION_TIMEOUT_SECONDS,
            cwd=str(worktree_dir),
            env=build_proxy_env(),
        )
        elapsed = time.monotonic() - started
        output = result.stdout + "\n" + result.stderr
        write_session_output_log(session_id, label, output)

        if is_rate_limited(output, AGENT_CODEX):
            log.warning("[codex] Session %d: rate limited (%.0fs)", session_id, elapsed)
            return False, output, elapsed
        if result.returncode != 0:
            log.warning("[codex] Session %d: exit code %d (%.0fs)", session_id, result.returncode, elapsed)
            return False, output, elapsed
        log.info("[codex] Session %d: completed (%.0fs)", session_id, elapsed)
        return True, output, elapsed
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        output = format_timeout_output(exc)
        write_session_output_log(session_id, label, output)
        log.error("[codex] Session %d: timed out (%.0fs)", session_id, elapsed)
        return False, output, elapsed


def run_claude_session(
    prompt: str,
    worktree_dir: Path,
    session_id: int,
    label: str,
    dry_run: bool = False,
) -> tuple[bool, str, float]:
    log.info("[claude] Session %d: %s — starting", session_id, label)
    if dry_run:
        log.info("[DRY RUN] claude prompt (%d chars)", len(prompt))
        return True, "[dry run]", 0.0

    write_session_prompt_log(session_id, label, prompt)
    started = time.monotonic()

    try:
        result = subprocess.run(
            [
                CLAUDE_BIN,
                "--print",
                "--dangerously-skip-permissions",
                "--model", "opus",
                "-p", prompt,
            ],
            capture_output=True, text=True,
            timeout=SESSION_TIMEOUT_SECONDS,
            cwd=str(worktree_dir),
            env=build_proxy_env(),
        )
        elapsed = time.monotonic() - started
        output = result.stdout + "\n" + result.stderr
        write_session_output_log(session_id, label, output)

        if is_rate_limited(output, AGENT_CLAUDE):
            log.warning("[claude] Session %d: rate limited (%.0fs)", session_id, elapsed)
            return False, output, elapsed
        if result.returncode != 0:
            log.warning("[claude] Session %d: exit code %d (%.0fs)", session_id, result.returncode, elapsed)
            return False, output, elapsed
        log.info("[claude] Session %d: completed (%.0fs)", session_id, elapsed)
        return True, output, elapsed
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        output = format_timeout_output(exc)
        write_session_output_log(session_id, label, output)
        log.error("[claude] Session %d: timed out (%.0fs)", session_id, elapsed)
        return False, output, elapsed


def run_session(
    agent_type: str, prompt: str, worktree_dir: Path,
    session_id: int, label: str, dry_run: bool = False,
) -> tuple[bool, str, float]:
    if agent_type == AGENT_CLAUDE:
        return run_claude_session(prompt, worktree_dir, session_id, label, dry_run)
    return run_codex_session(prompt, worktree_dir, session_id, label, dry_run)


# ---------------------------------------------------------------------------
# Agent selection (quota-aware, per-task)
# ---------------------------------------------------------------------------


@dataclass
class QuotaState:
    """Cached quota snapshot, refreshed periodically."""
    codex_available: bool = False
    effective_codex_slots: int = 0
    email: str = ""
    hourly_pct: int = 0
    last_checked: float = 0.0


_quota_state = QuotaState()
_QUOTA_REFRESH_INTERVAL = 60.0  # seconds


def refresh_quota(max_codex_parallel: int, force: bool = False) -> QuotaState:
    """Refresh the cached quota state if stale or forced."""
    now = time.monotonic()
    if not force and (now - _quota_state.last_checked) < _QUOTA_REFRESH_INTERVAL:
        return _quota_state

    codex_quota = get_codex_quota()
    available = codex_quota.available

    if not available:
        slots = 0
    elif codex_quota.hourly_pct < 5:
        slots = 0
    elif codex_quota.hourly_pct < 15:
        slots = min(1, max_codex_parallel)
    elif codex_quota.hourly_pct < 30:
        slots = max(1, max_codex_parallel // 2)
    else:
        slots = max_codex_parallel

    if available and slots > 0:
        switch_codex_to_best_once()

    _quota_state.codex_available = available
    _quota_state.effective_codex_slots = slots
    _quota_state.email = codex_quota.email
    _quota_state.hourly_pct = codex_quota.hourly_pct
    _quota_state.last_checked = now

    log.info(
        "Quota refresh: %s %d%% → effective codex slots: %d/%d",
        codex_quota.email, codex_quota.hourly_pct, slots, max_codex_parallel,
    )
    return _quota_state


def resolve_agent_for_task(
    task: TaskCard,
    current_codex_running: int,
    max_codex_parallel: int,
) -> str:
    """Pick agent for a single task given current pool state."""
    qs = refresh_quota(max_codex_parallel)

    if task.agent == AGENT_CLAUDE:
        return AGENT_CLAUDE
    if task.agent == AGENT_CODEX:
        return AGENT_CODEX  # honor explicit even if over budget
    # auto: codex if slot available
    if current_codex_running < qs.effective_codex_slots:
        return AGENT_CODEX
    return AGENT_CLAUDE


# ---------------------------------------------------------------------------
# Parallel task worker
# ---------------------------------------------------------------------------


def run_task_worker(
    task: TaskCard,
    agent_type: str,
    worktree_dir: Path,
    dry_run: bool,
) -> TaskResult:
    """Execute a single task in a thread. Returns TaskResult."""
    sid = next_session_id()
    label = f"{task.task_id}_work"
    prompt = build_task_prompt(task, worktree_dir)

    success, output, elapsed = run_session(
        agent_type, prompt, worktree_dir, sid, label, dry_run,
    )
    rate_limited = not success and is_rate_limited(output, agent_type)

    return TaskResult(
        task=task,
        session_id=sid,
        agent_type=agent_type,
        success=success,
        output=output,
        elapsed=elapsed,
        rate_limited=rate_limited,
    )


# ---------------------------------------------------------------------------
# Git operations (thread-safe)
# ---------------------------------------------------------------------------


def commit_worktree(
    worktree_dir: Path,
    session_id: int,
    task_id: str,
    dry_run: bool = False,
) -> bool:
    if dry_run:
        log.info("Skipping git commit for session %d in dry-run mode", session_id)
        return True
    with _git_lock:
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree_dir, capture_output=True, text=True,
            )
            if not status.stdout.strip():
                log.info("No changes to commit after session %d", session_id)
                return True
            subprocess.run(["git", "add", "."], cwd=worktree_dir, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"baseline repro session {session_id}: {task_id}"],
                cwd=worktree_dir, check=True,
            )
            log.info("Committed session %d (%s)", session_id, task_id)
            return True
        except subprocess.CalledProcessError as e:
            log.error("Git commit failed for session %d: %s", session_id, e)
            return False


# ---------------------------------------------------------------------------
# Task status check
# ---------------------------------------------------------------------------


def is_task_done(task: TaskCard, worktree_dir: Path) -> bool:
    tasks_dir = worktree_dir / "baseline" / "research_context" / "tasks"
    task_path = tasks_dir / task.filename
    if not task_path.exists():
        archive_path = find_archived_task_card(tasks_dir, task.filename)
        return archive_path is not None
    content = task_path.read_text(encoding="utf-8")
    return "Status: DONE" in content or "Status: ✅" in content


# ---------------------------------------------------------------------------
# Main loop — continuous pool dispatch
#
# Model: maintain a pool of up to max_parallel running tasks.
# When ANY task completes, immediately commit its results, check DAG
# for newly-unblocked tasks, and fill the freed slot.
#
# This means t01 finishing instantly triggers t02 even while t03–t11
# are still running.
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Baseline reproduction task scheduler")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-sessions", type=int, default=60,
                        help="Total session budget (including retries)")
    parser.add_argument("--max-parallel", type=int, default=6,
                        help="Max concurrent sessions in the pool")
    parser.add_argument("--max-codex-parallel", type=int, default=3,
                        help="Max concurrent codex sessions (quota control)")
    parser.add_argument("--max-retries-per-task", type=int, default=5)
    parser.add_argument(
        "--use-current-workspace",
        action="store_true",
        help="Run directly in the current repository workspace instead of creating a worktree",
    )
    parser.add_argument("--worktree-id", type=str, default=DEFAULT_WORKTREE_ID)
    parser.add_argument(
        "--start-point-worktree-id", type=str, default=None,
        help="Optional source worktree ID whose HEAD seeds the target worktree",
    )
    parser.add_argument(
        "--sparse-path",
        action="append",
        default=[],
        help=(
            "Limit the worktree checkout to a repo-relative path. "
            "Repeat to include multiple paths, e.g. --sparse-path baseline"
        ),
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    worktree_dir = prepare_workspace(args)

    global LOG_DIR
    run_ts = time.strftime("%Y%m%dT%H%M%S")
    LOG_DIR = LOG_BASE / f"run_{run_ts}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Baseline Task Scheduler starting (continuous pool)")
    log.info("Repo: %s | Worktree: %s", REPO_ROOT, worktree_dir)
    log.info("Max sessions: %d | Pool size: %d | Codex cap: %d",
             args.max_sessions, args.max_parallel, args.max_codex_parallel)
    log.info("=" * 60)

    wt_tasks_dir = worktree_dir / "baseline" / "research_context" / "tasks"
    wt_dag_file = worktree_dir / "baseline" / "research_context" / "dag.md"
    primary_task_archive_dir(wt_tasks_dir).mkdir(parents=True, exist_ok=True)

    task_retries: dict[str, int] = {}
    in_flight: set[str] = set()            # task_ids currently running
    codex_running = 0                       # count of running codex sessions
    executor = ThreadPoolExecutor(max_workers=args.max_parallel)
    live_futures: dict[object, TaskCard] = {}  # future -> TaskCard
    future_agents: dict[object, str] = {}      # future -> agent_type

    def fill_pool() -> None:
        """Submit new tasks until the pool is full or nothing is dispatchable."""
        nonlocal codex_running
        while len(live_futures) < args.max_parallel and _session_counter < args.max_sessions:
            reconcile_task_cards(wt_dag_file, wt_tasks_dir)
            dispatchable = find_dispatchable_tasks(wt_dag_file, wt_tasks_dir)
            # skip tasks already running or that exceeded retries
            candidates = [
                t for t in dispatchable
                if t.task_id not in in_flight
                and task_retries.get(t.task_id, 0) < args.max_retries_per_task
            ]
            if not candidates:
                break

            task = candidates[0]
            agent_type = resolve_agent_for_task(
                task, codex_running, args.max_codex_parallel,
            )

            # If codex required but all codex slots full, skip until a slot frees
            if task.agent == AGENT_CODEX and agent_type == AGENT_CLAUDE:
                # auto would have fallen back to claude; explicit codex → wait
                break

            future = executor.submit(
                run_task_worker, task, agent_type, worktree_dir, args.dry_run,
            )
            live_futures[future] = task
            future_agents[future] = agent_type
            in_flight.add(task.task_id)
            if agent_type == AGENT_CODEX:
                codex_running += 1
            log.info(
                "Pool +%s [%s] (pool=%d, codex=%d/%d)",
                task.task_id, agent_type,
                len(live_futures), codex_running, args.max_codex_parallel,
            )

    def handle_completed(future: object) -> None:
        """Process a completed future: commit, evaluate, update counters."""
        nonlocal codex_running
        task = live_futures.pop(future)
        agent_type = future_agents.pop(future)
        in_flight.discard(task.task_id)
        if agent_type == AGENT_CODEX:
            codex_running = max(0, codex_running - 1)

        try:
            result: TaskResult = future.result()
        except Exception as exc:
            log.error("%s raised exception: %s", task.task_id, exc)
            retries = task_retries.get(task.task_id, 0) + 1
            task_retries[task.task_id] = retries
            return

        status_str = "DONE" if result.success else "FAILED"
        if result.rate_limited:
            status_str = "RATE_LIMITED"
        log.info(
            "%s [%s] session=%d %s (%.0fs)",
            result.task.task_id, result.agent_type,
            result.session_id, status_str, result.elapsed,
        )

        # Commit this task's changes immediately
        commit_worktree(
            worktree_dir,
            result.session_id,
            result.task.task_id,
            dry_run=args.dry_run,
        )

        done = is_task_done(result.task, worktree_dir)
        if done:
            log.info("Task %s: COMPLETED ✓", result.task.task_id)
            task_retries.pop(result.task.task_id, None)
        else:
            retries = task_retries.get(result.task.task_id, 0) + 1
            task_retries[result.task.task_id] = retries
            log.info(
                "Task %s: not done (attempt %d/%d)%s",
                result.task.task_id, retries, args.max_retries_per_task,
                " [rate limited]" if result.rate_limited else "",
            )

            # If rate-limited and nothing else is running, sleep
            if result.rate_limited and len(live_futures) == 0:
                quota = get_codex_quota()
                sleep_until_codex_reset(quota)
                refresh_quota(args.max_codex_parallel, force=True)

    # --- Main loop ---
    try:
        fill_pool()

        while live_futures:
            # Wait for ANY one task to complete
            done_futures = set()
            while not done_futures:
                for f in list(live_futures.keys()):
                    if f.done():
                        done_futures.add(f)
                if not done_futures:
                    time.sleep(1)

            # Handle all completed futures
            for f in done_futures:
                handle_completed(f)

            # Budget check
            if _session_counter >= args.max_sessions:
                log.warning("Hit max sessions limit (%d). Draining pool.", args.max_sessions)
                break

            # Fill freed slots — this is where downstream tasks get picked up
            fill_pool()

        # Drain any remaining futures if we broke out early
        for f in list(live_futures.keys()):
            f.result()  # wait
            handle_completed(f) if f in live_futures else None

    finally:
        executor.shutdown(wait=True)

    # --- Summary ---
    reconcile_task_cards(wt_dag_file, wt_tasks_dir)
    remaining = tasks_remaining(wt_tasks_dir)
    log.info("=" * 60)
    log.info("Scheduler finished: %d sessions", _session_counter)
    log.info("Tasks remaining: %d", len(remaining))
    if remaining:
        log.info("  %s", [f.name for f in remaining])
    log.info("Worktree: %s", worktree_dir)
    log.info("Review and merge manually.")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
