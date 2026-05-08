"""Shared rich Console + banner + theme helpers for the mini-cyber CLI.

Keep all visual constants in one place so subcommands look consistent.
"""
from __future__ import annotations

from rich.box import HEAVY, ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "brand": "bold magenta",
        "brand.dim": "magenta",
        "ok": "bold green",
        "warn": "bold yellow",
        "fail": "bold red",
        "info": "cyan",
        "step": "bold cyan",
        "thought": "italic dim",
        "action": "bold yellow",
        "observation": "white",
        "success": "bold green",
        "metric.label": "bold blue",
        "metric.value": "bright_white",
    }
)

console: Console = Console(theme=THEME, highlight=False)


_BANNER = r"""
   ____      _                    ____           ____   __ _____
  / ___|   _| |__   ___ _ __     / ___|___  ___ / ___| / /|  _  |
 | |  | | | | '_ \ / _ \ '__|   | |   / _ \/ __| |    / / | |_) |
 | |__| |_| | |_) |  __/ |      | |__|  __/ (__| |___/ /  |  _ <
  \____\__, |_.__/ \___|_|       \____\___|\___|\____/_/   |_| \_\
       |___/                                ___
                                           / _ \ _ __ ___ _ __   __ _
                                          / /_)/ '__/ _ \ '_ \ / _` |
                                         / ___/| | |  __/ | | | (_| |
                                         \/    |_|  \___|_| |_|\__,_|
"""


def print_banner(subtitle: str | None = None) -> None:
    """Print the cybersec_arena banner. Call from the top of long-running commands."""
    text = Text(_BANNER, style="brand", justify="left")
    body = text
    if subtitle:
        body = Text.assemble(text, "\n", (subtitle, "brand.dim"))
    console.print(Panel(body, border_style="brand", box=ROUNDED, padding=(0, 2)))


def print_section(title: str, body: str | Text | None = None, *, style: str = "info") -> None:
    """Box-drawn section header used by subcommands."""
    panel = Panel(
        body if body is not None else "",
        title=f"[{style}]{title}[/{style}]",
        border_style=style,
        box=HEAVY,
    )
    console.print(panel)


def kv(label: str, value: object, *, label_style: str = "metric.label") -> Text:
    """Format a key-value pair like '  Label : value' for inline use."""
    text = Text()
    text.append(f"{label:<22}", style=label_style)
    text.append(str(value), style="metric.value")
    return text
