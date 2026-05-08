"""Trajectory inspector: render an agent log file as colored panels.

Agent runs write a flat log under ``logs/.../<chal_id>_run<N>.log`` whose
lines look like::

    2026-05-06 12:34:56 - INFO - System Prompt: ...
    2026-05-06 12:34:57 - INFO - Step 1: ...
    2026-05-06 12:34:58 - INFO - Action: ...

This subcommand parses those steps and prints them as syntax-highlighted
panels so you can read a long trajectory without scrolling raw log noise.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Iterator

from rich.box import ROUNDED
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from ._theme import console, print_banner, print_section

_LOG_LINE = re.compile(r"^(?P<ts>[\d-]+\s[\d:,]+)\s*-\s*(?P<lvl>\w+)\s*-\s*(?P<msg>.*)$")


def _iter_log_records(text: str) -> Iterator[tuple[str, str, str]]:
    """Yield (timestamp, level, message-block) tuples.

    A "record" continues across multiple lines until the next line that
    starts with a ``YYYY-MM-DD HH:MM:SS`` timestamp.
    """
    cur_ts = ""
    cur_lvl = ""
    cur_msg: list[str] = []
    for raw in text.splitlines():
        m = _LOG_LINE.match(raw)
        if m:
            if cur_ts:
                yield cur_ts, cur_lvl, "\n".join(cur_msg)
            cur_ts, cur_lvl, body = m.group("ts"), m.group("lvl"), m.group("msg")
            cur_msg = [body]
        else:
            cur_msg.append(raw)
    if cur_ts:
        yield cur_ts, cur_lvl, "\n".join(cur_msg)


_TAGS = (
    ("System Prompt", "system", "magenta"),
    ("Instance prompt", "user", "cyan"),
    ("Thought", "thought", "yellow"),
    ("Action", "action", "yellow"),
    ("Observation", "observation", "green"),
    ("Step ", "step", "blue"),
    ("Finished.", "result", "bold green"),
    ("Run failed", "fail", "bold red"),
)


def _classify(message: str) -> tuple[str, str, str]:
    for prefix, kind, color in _TAGS:
        if message.startswith(prefix):
            return kind, color, prefix
    return "log", "dim", ""


def _strip_prefix(message: str, prefix: str) -> str:
    if not prefix:
        return message
    body = message[len(prefix):].lstrip(":").strip()
    return body


def _render_message_body(message: str, kind: str) -> Markdown | Syntax | Text:
    if kind in {"action", "thought"} and "```" in message:
        # Action blocks usually contain ```bash ... ``` — render as markdown so the code highlights.
        return Markdown(message)
    if kind in {"system", "user"}:
        return Markdown(message)
    if kind == "observation" and len(message) > 4000:
        message = message[:4000] + "\n... (truncated)"
    return Text(message)


def render_trajectory(path: Path, *, max_steps: int | None = None) -> None:
    """Render the trajectory at *path*. Prints to the shared console."""
    if not path.exists():
        console.print(f"[fail]Trajectory file not found:[/fail] {path}")
        raise SystemExit(2)

    print_banner(subtitle=f"trajectory inspector — {path.name}")
    text = path.read_text(encoding="utf-8", errors="replace")

    rendered_steps = 0
    for ts, lvl, msg in _iter_log_records(text):
        kind, color, prefix = _classify(msg)
        if kind == "log" and lvl != "ERROR":
            continue
        title_parts = [f"[{color}]{kind}[/{color}]"]
        if prefix.startswith("Step "):
            title_parts.append(f"[dim]{ts}[/dim]")
        else:
            title_parts.append(f"[dim]{ts}[/dim]")
        body = _strip_prefix(msg, prefix)
        panel = Panel(
            _render_message_body(body, kind),
            title=" · ".join(title_parts),
            border_style=color,
            box=ROUNDED,
            padding=(0, 1),
        )
        console.print(panel)
        if kind == "step":
            rendered_steps += 1
            if max_steps and rendered_steps >= max_steps:
                console.print(Rule(f"[warn]reached --max-steps={max_steps}[/warn]"))
                return

    console.print(Rule("[brand.dim]end of trajectory[/brand.dim]"))


def list_trajectories(root: Path) -> list[Path]:
    """Return all *.log files under root, sorted by mtime descending."""
    if not root.exists():
        return []
    files = list(root.rglob("*.log"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def render_trajectory_index(files: Iterable[Path], limit: int = 30) -> None:
    """Print a table of recent trajectory files."""
    from rich.table import Table

    table = Table(title="Recent trajectories", border_style="brand")
    table.add_column("#", justify="right", style="metric.label")
    table.add_column("Path", style="metric.value", overflow="fold")
    table.add_column("Size", justify="right", style="info")
    for i, p in enumerate(list(files)[:limit], 1):
        size_kb = p.stat().st_size / 1024
        table.add_row(str(i), str(p), f"{size_kb:,.1f} KB")
    console.print(table)
    print_section(
        "Open one",
        body=Text.from_markup(
            "Use [ok]mini-cyber inspect <path>[/ok] to render a specific trajectory."
        ),
        style="brand.dim",
    )
