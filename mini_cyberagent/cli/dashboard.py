"""Live dashboard for an in-progress evolution run.

Watches a run directory written by ``run_evolve_batch_skill.py``:

    <run_dir>/
        <category>/<chal_id>/
            gen_0_stats.json
            gen_1_stats.json
            ...

Each `gen_N_stats.json` is a list of EvolutionNode dataclass dumps with
fields like ``node_id, success_rate, avg_steps, avg_token_num,
assessment_score, total_runs``.

We render two tables side-by-side: per-challenge generation summary and a
top-K leaderboard. The view refreshes every ``refresh_seconds``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rich.box import ROUNDED
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ._theme import console, print_banner


@dataclass
class _ChallengeStatus:
    chal_id: str
    category: str
    generation: int
    nodes: int
    best_sr: float
    best_node_id: str
    best_score: float
    avg_steps: float
    last_update: float


def _load_node_dump(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _scan(run_dir: Path) -> list[_ChallengeStatus]:
    out: list[_ChallengeStatus] = []
    if not run_dir.exists():
        return out
    for chal_dir in run_dir.glob("*/*"):
        if not chal_dir.is_dir():
            continue
        gen_files = sorted(chal_dir.glob("gen_*_stats.json"), key=lambda p: int(p.stem.split("_")[1]))
        if not gen_files:
            continue
        latest = gen_files[-1]
        nodes = _load_node_dump(latest)
        if not nodes:
            continue
        best = max(nodes, key=lambda n: n.get("success_rate", 0.0))
        gen_idx = int(latest.stem.split("_")[1])
        out.append(
            _ChallengeStatus(
                chal_id=chal_dir.name,
                category=chal_dir.parent.name,
                generation=gen_idx,
                nodes=len(nodes),
                best_sr=float(best.get("success_rate", 0.0)),
                best_node_id=str(best.get("node_id", "?")),
                best_score=float(best.get("assessment_score", 0.0)),
                avg_steps=float(best.get("avg_steps", 0.0)),
                last_update=latest.stat().st_mtime,
            )
        )
    return out


def _build_status_table(rows: list[_ChallengeStatus]) -> Table:
    table = Table(title="Per-challenge progress", border_style="info", box=ROUNDED, expand=True)
    table.add_column("Challenge", style="metric.value")
    table.add_column("Cat", style="metric.label")
    table.add_column("Gen", justify="right")
    table.add_column("Nodes", justify="right")
    table.add_column("Best SR", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Steps", justify="right")
    rows = sorted(rows, key=lambda r: -r.best_sr)
    for r in rows:
        sr_color = "ok" if r.best_sr >= 0.3 else ("warn" if r.best_sr > 0 else "fail")
        table.add_row(
            r.chal_id,
            r.category,
            str(r.generation),
            str(r.nodes),
            f"[{sr_color}]{r.best_sr * 100:5.1f}%[/{sr_color}]",
            f"{r.best_score:5.1f}",
            f"{r.avg_steps:5.1f}",
        )
    return table


def _build_leaderboard(rows: list[_ChallengeStatus], top_n: int = 10) -> Table:
    table = Table(title=f"Top {top_n} challenges", border_style="ok", box=ROUNDED, expand=True)
    table.add_column("#", justify="right", style="metric.label")
    table.add_column("Challenge", style="metric.value")
    table.add_column("Best node", style="info")
    table.add_column("SR", justify="right")
    rows = sorted(rows, key=lambda r: -r.best_sr)[:top_n]
    for i, r in enumerate(rows, 1):
        table.add_row(str(i), r.chal_id, r.best_node_id, f"{r.best_sr * 100:5.1f}%")
    return table


def _build_aggregates(rows: list[_ChallengeStatus]) -> Panel:
    if not rows:
        return Panel("No generations recorded yet.", title="Aggregates", border_style="warn")
    n = len(rows)
    solved = sum(1 for r in rows if r.best_sr >= 0.3)
    avg_sr = sum(r.best_sr for r in rows) / n
    avg_score = sum(r.best_score for r in rows) / n
    max_gen = max(r.generation for r in rows)
    body = Text.assemble(
        ("Challenges    ", "metric.label"), (f"{n}\n", "metric.value"),
        ("Solved (≥30%) ", "metric.label"), (f"{solved}/{n}\n", "ok"),
        ("Avg SR        ", "metric.label"), (f"{avg_sr * 100:.1f}%\n", "metric.value"),
        ("Avg score     ", "metric.label"), (f"{avg_score:.1f}\n", "metric.value"),
        ("Max generation", "metric.label"), (f"{max_gen}", "metric.value"),
    )
    return Panel(body, title="Aggregates", border_style="brand", box=ROUNDED)


def _build_layout(run_dir: Path, rows: list[_ChallengeStatus]) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )
    layout["body"].split_row(
        Layout(_build_status_table(rows), name="status", ratio=2),
        Layout(name="right"),
    )
    layout["right"].split_column(
        Layout(_build_aggregates(rows), name="agg"),
        Layout(_build_leaderboard(rows), name="board"),
    )
    header_text = Text.assemble(
        ("evolution dashboard ", "brand"),
        ("· ", "brand.dim"),
        (str(run_dir), "info"),
        ("  ", ""),
        (f"({len(rows)} challenges)", "metric.label"),
    )
    layout["header"].update(Panel(header_text, border_style="brand", box=ROUNDED))
    return layout


def watch(run_dir: Path, *, refresh_seconds: float = 5.0) -> None:
    """Open a live dashboard. Runs until Ctrl-C."""
    print_banner(subtitle="evolution dashboard")
    if not run_dir.exists():
        console.print(f"[warn]Run directory does not exist yet:[/warn] {run_dir}")
        console.print("[brand.dim]Waiting for it to appear …[/brand.dim]")

    with Live(_build_layout(run_dir, []), console=console, refresh_per_second=2, screen=False) as live:
        try:
            while True:
                rows = _scan(run_dir)
                live.update(_build_layout(run_dir, rows))
                time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            console.print("\n[warn]Dashboard stopped.[/warn]")
