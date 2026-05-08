from __future__ import annotations

from typing import Iterable


def format_worker_phase_message(
    chal_id: str,
    pid: int,
    phase: str,
    detail: str | None = None,
) -> str:
    parts = [
        f"worker state chal={chal_id}",
        f"pid={pid}",
        f"phase={phase}",
    ]
    if detail:
        parts.append(detail)
    return " | ".join(parts)


def format_task_completion_message(
    node_id: str,
    sample_id: int,
    completed: int,
    total: int,
    success: bool,
    steps: int,
    duration_s: float,
    token_num: int,
) -> str:
    return (
        "task completion "
        f"completed={completed}/{total} "
        f"node={node_id} "
        f"sample={sample_id} "
        f"success={success} "
        f"steps={steps} "
        f"duration={duration_s:.2f}s "
        f"tokens={token_num}"
    )


def format_scheduler_interrupt_message(
    completed: int,
    total: int,
    pending_tasks: Iterable[str],
) -> str:
    pending = list(pending_tasks)
    pending_summary = ", ".join(pending[:5]) if pending else "-"
    return (
        "scheduler interrupted "
        f"completed={completed}/{total} "
        f"pending={len(pending)} "
        f"pending_tasks=[{pending_summary}]"
    )


def format_scheduler_result_error_message(
    node_id: str,
    sample_id: int,
    stage: str,
    exc: BaseException,
) -> str:
    return (
        "scheduler task result error "
        f"stage={stage} "
        f"node={node_id} "
        f"sample={sample_id} "
        f"exception_type={type(exc).__name__} "
        f"exception_message={exc}"
    )
