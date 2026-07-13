"""Shared look & feel for the botmgr terminal UI."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

_THEME = Theme(
    {
        "accent": "bold cyan",
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "muted": "grey62",
        "key": "cyan",
        "value": "white",
    }
)

console = Console(theme=_THEME, highlight=False)

_BANNER = r"""
 ____   ___ _____   __  __    _    _   _    _    ____ _____ ____
| __ ) / _ \_   _| |  \/  |  / \  | \ | |  / \  / ___| ____|  _ \
|  _ \| | | || |   | |\/| | / _ \ |  \| | / _ \| |  _|  _| | |_) |
| |_) | |_| || |   | |  | |/ ___ \| |\  |/ ___ \ |_| | |___|  _ <
|____/ \___/ |_|   |_|  |_/_/   \_\_| \_/_/   \_\____|_____|_| \_\
"""


def banner(subtitle: str = "") -> None:
    text = Text(_BANNER.strip("\n"), style="accent")
    console.print(Panel(text, subtitle=subtitle or None, border_style="cyan", padding=(0, 2)))


def step(index: int, total: int, title: str) -> None:
    console.print()
    console.print(f"[accent]Step {index}/{total}[/accent] · [bold]{title}[/bold]")
    console.print("[muted]" + "─" * 60 + "[/muted]")


def ok(message: str) -> None:
    console.print(f"[ok]✓[/ok] {message}")


def warn(message: str) -> None:
    console.print(f"[warn]![/warn] {message}")


def fail(message: str) -> None:
    console.print(f"[err]✗[/err] {message}")
