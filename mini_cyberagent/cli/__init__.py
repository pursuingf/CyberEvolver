"""``mini-cyber`` — command-line entry point.

Subcommands:

  solve        Run an agent on a single challenge (interactive).
  batch        Run an agent on many challenges in parallel.
  evolve       Drive the evolution loop end-to-end.
  inspect      Render a saved trajectory log as colored panels.
  dashboard    Live TUI for an in-progress evolution run.
  serve        Start the bench_hub challenge server.
  models       List models declared in common/configs/model.yml.
  benchmarks   List benchmarks discovered in bench_hub/benchmarks.

The CLI is intentionally a thin wrapper around the existing run_*.py
entry scripts: each subcommand assembles argv and shells out to the
script, so we never re-implement the underlying logic.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ._theme import console, kv, print_banner, print_section

REPO_ROOT = Path(__file__).resolve().parents[2]

app = typer.Typer(
    name="mini-cyber",
    rich_markup_mode="rich",
    help=(
        "[brand]mini-cyber[/brand] — command-line interface for the "
        "[bold]cybersec_arena[/bold] research framework.\n\n"
        "[brand.dim]Run [ok]mini-cyber <subcommand> --help[/ok] for details on each.[/brand.dim]"
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _shell_out(argv: list[str], *, env_extra: Optional[dict] = None) -> int:
    """Run argv as a subprocess inheriting the current environment."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    cmd_str = " ".join(shlex.quote(a) for a in argv)
    console.print(Panel(f"[ok]$ {cmd_str}[/ok]", border_style="ok", box=ROUNDED, padding=(0, 1)))
    return subprocess.call(argv, env=env, cwd=str(REPO_ROOT))


# ====================================================================
# solve  — single-challenge debug run
# ====================================================================
@app.command(help="Run an agent on a [bold]single challenge[/bold] (interactive debug).")
def solve(
    challenge_id: str = typer.Argument(..., help="Challenge ID, e.g. ic-crypto-5."),
    model: str = typer.Option("DeepSeek-V3.1", "-m", "--model", help="Model name from model.yml"),
    config: str = typer.Option(
        "mini_cyberagent/configs/raw_ctf.yaml", "-c", "--config",
        help="Agent run config YAML.",
    ),
    step_limit: int = typer.Option(20, "--step-limit", help="Max agent steps."),
    benchmark: Optional[str] = typer.Option(None, "--benchmark", help="Benchmark tag (defaults to whatever the config provides)."),
    extra: Optional[list[str]] = typer.Option(None, "--extra", help="Extra raw flags forwarded to run_single_debug.py."),
) -> None:
    print_banner(subtitle=f"solve · {challenge_id} · model={model}")
    argv = [
        sys.executable,
        str(REPO_ROOT / "run_single_debug.py"),
        "--config", config,
        "--model", model,
        "--challenge-id", challenge_id,
        "--step-limit", str(step_limit),
    ]
    if benchmark:
        argv += ["--benchmark", benchmark]
    if extra:
        argv += list(extra)
    raise typer.Exit(_shell_out(argv))


# ====================================================================
# batch  — multi-challenge non-evolution
# ====================================================================
@app.command(help="Run an agent on a [bold]batch of challenges[/bold] in parallel.")
def batch(
    benchmark: str = typer.Option(..., "--benchmark", help="Benchmark tag (e.g. cybench, nyu_ctf)."),
    model: str = typer.Option("DeepSeek-V3.1", "-m", "--model"),
    config: str = typer.Option("mini_cyberagent/configs/mini_ctf.yaml", "-c", "--config"),
    max_workers: int = typer.Option(8, "-w", "--max-workers", help="Parallel worker count."),
    category: Optional[str] = typer.Option(None, "--category", help="Comma-separated categories."),
    extra: Optional[list[str]] = typer.Option(None, "--extra", help="Extra raw flags forwarded to run_batch.py."),
) -> None:
    print_banner(subtitle=f"batch · {benchmark} · {max_workers} workers · model={model}")
    argv = [
        sys.executable,
        str(REPO_ROOT / "run_batch.py"),
        "--config", config,
        "--model", model,
        "--benchmark", benchmark,
        "--max-workers", str(max_workers),
    ]
    if category:
        argv += ["--category", category]
    if extra:
        argv += list(extra)
    raise typer.Exit(_shell_out(argv))


# ====================================================================
# evolve  — evolutionary run
# ====================================================================
@app.command(help="Drive the [bold]evolution loop[/bold] over one or more challenges.")
def evolve(
    benchmark: str = typer.Option("cybench", "--benchmark"),
    model: str = typer.Option("DeepSeek-V3.1", "-m", "--model"),
    config_mode: str = typer.Option("evo", "--config-mode", help="One of: evo | evo_no_beam | raw"),
    challenge_id: Optional[str] = typer.Option(None, "-i", "--challenge-id", help="Run a single challenge."),
    ids: Optional[str] = typer.Option(None, "--ids", help="Comma-separated challenge IDs."),
    category: Optional[str] = typer.Option(None, "-c", "--category"),
    base_seed_path: str = typer.Option(
        "./cyber_evolver/gen0_root/skill_based", "--base_seed_path",
        help="Seed agent directory.",
    ),
    evolve_prompt_cfg: str = typer.Option(
        "cyber_evolver/evolve/prompt_skill.yml", "--evolve_prompt_cfg",
        help="Mutation prompt config.",
    ),
    max_workers: int = typer.Option(1, "-w", "--max-workers", help="Parallel challenges."),
    task_workers: int = typer.Option(6, "--task_workers", help="Parallel agent samples per challenge."),
    extra: Optional[list[str]] = typer.Option(None, "--extra", help="Extra raw flags."),
) -> None:
    print_banner(subtitle=f"evolve · {benchmark} · mode={config_mode} · model={model}")
    argv = [
        sys.executable,
        str(REPO_ROOT / "run_evolve_batch_skill.py"),
        "--config-mode", config_mode,
        "--benchmark", benchmark,
        "--model", model,
        "--base_seed_path", base_seed_path,
        "--evolve_prompt_cfg", evolve_prompt_cfg,
        "--max-workers", str(max_workers),
        "--task_workers", str(task_workers),
    ]
    if challenge_id:
        argv += ["--challenge-id", challenge_id]
    if ids:
        argv += ["--ids", ids]
    if category:
        argv += ["--category", category]
    if extra:
        argv += list(extra)
    raise typer.Exit(_shell_out(argv))


# ====================================================================
# inspect  — trajectory viewer
# ====================================================================
@app.command(help="Render a saved trajectory log as colored panels.")
def inspect(
    path: Optional[Path] = typer.Argument(None, help="Trajectory .log file. If omitted, list recent ones."),
    root: Path = typer.Option(REPO_ROOT / "logs", "--root", help="Search root for trajectories."),
    max_steps: Optional[int] = typer.Option(None, "--max-steps", help="Stop rendering after N agent steps."),
) -> None:
    from . import inspector

    if path is None:
        files = inspector.list_trajectories(root)
        if not files:
            console.print(f"[warn]No trajectories found under[/warn] {root}")
            raise typer.Exit(1)
        inspector.render_trajectory_index(files)
        raise typer.Exit(0)
    inspector.render_trajectory(path, max_steps=max_steps)


# ====================================================================
# dashboard  — live evolution monitor
# ====================================================================
@app.command(name="dashboard", help="Live TUI dashboard tracking an in-progress evolution run.")
def dashboard_cmd(
    run_dir: Path = typer.Argument(..., help="Run directory written by run_evolve_batch_skill.py."),
    refresh: float = typer.Option(5.0, "--refresh", help="Refresh interval (seconds)."),
) -> None:
    # Submodule import via importlib so the typer command function name
    # ('dashboard_cmd') doesn't collide with the 'dashboard' submodule lookup.
    import importlib

    _dashboard = importlib.import_module("mini_cyberagent.cli.dashboard")
    _dashboard.watch(run_dir, refresh_seconds=refresh)


# ====================================================================
# serve  — start the FastAPI challenge server
# ====================================================================
@app.command(help="Start the [bold]bench_hub[/bold] challenge server.")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    target_runtime: bool = typer.Option(False, "--target-runtime", help="Start target_runtime_server instead of challenge_server."),
) -> None:
    print_banner(subtitle=f"serve · {'target_runtime' if target_runtime else 'challenge_server'} on {host}:{port}")
    script = "target_runtime_server.py" if target_runtime else "challenge_server.py"
    argv = [sys.executable, str(REPO_ROOT / "bench_hub" / "server" / script), host, str(port)]
    raise typer.Exit(_shell_out(argv))


# ====================================================================
# models  — list configured LLM endpoints
# ====================================================================
@app.command(help="List models declared in common/configs/model.yml.")
def models() -> None:
    print_banner(subtitle="model registry")
    try:
        import yaml
    except ImportError:
        console.print("[fail]PyYAML not installed.[/fail]")
        raise typer.Exit(1)

    candidates = [
        REPO_ROOT / "common" / "configs" / "model.yml",
        REPO_ROOT / "common" / "configs" / "model.yml.example",
    ]
    cfg_path: Path | None = next((p for p in candidates if p.exists()), None)
    if cfg_path is None:
        console.print(f"[fail]No model config found at {candidates[0]}[/fail]")
        raise typer.Exit(2)
    console.print(kv("Source", str(cfg_path)))
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    table = Table(title="Models", border_style="brand", box=ROUNDED)
    table.add_column("Name", style="metric.value")
    table.add_column("Model", style="info")
    table.add_column("Base URL", style="metric.label", overflow="fold")
    for name in sorted(raw.keys()):
        entry = raw[name] or {}
        table.add_row(name, str(entry.get("model", "—")), str(entry.get("openai_api_base", "—")))
    console.print(table)


# ====================================================================
# benchmarks  — list adapters/JSON specs available locally
# ====================================================================
@app.command(help="List benchmarks available under bench_hub/benchmarks.")
def benchmarks() -> None:
    print_banner(subtitle="benchmark registry")
    bench_root = REPO_ROOT / "bench_hub" / "benchmarks"
    table = Table(title="Benchmarks", border_style="brand", box=ROUNDED)
    table.add_column("Benchmark", style="metric.value")
    table.add_column("Index file", style="info")
    table.add_column("Layout dir", style="metric.label")
    if not bench_root.exists():
        console.print(f"[warn]bench_hub/benchmarks does not exist:[/warn] {bench_root}")
        raise typer.Exit(1)
    for index in sorted(bench_root.glob("*.json")):
        layout = bench_root / index.stem
        layout_state = "yes" if layout.exists() else "[warn]missing[/warn]"
        table.add_row(index.stem, str(index.relative_to(REPO_ROOT)), layout_state)
    console.print(table)
    print_section(
        "Note",
        body=Text.from_markup(
            "Heavy fixture trees (autopenbench/, cvebench/, cybench/, nyu_ctf/, …) are\n"
            "gitignored. If a benchmark says [warn]missing[/warn], place the fixture\n"
            "tree under [info]bench_hub/benchmarks/<name>/[/info] before running."
        ),
        style="brand.dim",
    )


# ====================================================================
# version
# ====================================================================
@app.command(help="Print version and environment info.")
def version() -> None:
    import platform
    print_banner(subtitle="environment")
    console.print(kv("Python", platform.python_version()))
    console.print(kv("Repo root", str(REPO_ROOT)))
    console.print(kv("Platform", f"{platform.system()} {platform.release()}"))
    try:
        import importlib.metadata as md
        for pkg in ("typer", "rich", "fastapi", "pydantic", "httpx", "docker", "psutil"):
            try:
                console.print(kv(pkg, md.version(pkg)))
            except md.PackageNotFoundError:
                console.print(kv(pkg, "[warn]not installed[/warn]"))
    except Exception as exc:
        console.print(f"[warn]Could not introspect installed packages: {exc}[/warn]")


def main() -> None:
    """Entry point used by the ``mini-cyber`` console script."""
    app()


if __name__ == "__main__":
    main()
