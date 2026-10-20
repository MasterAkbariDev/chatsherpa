"""Rich-powered presentation helpers for the CLI."""

from __future__ import annotations

from rich.markup import escape
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

TEAL = "#2fc7b4"
VIOLET = "#9b8bf0"
AMBER = "#e8a34d"
CORAL = "#f0725c"
GOOD = "#4cc488"
SOFT = "grey62"

PROVIDER_STYLE = {
    "chatgpt": GOOD,
    "deepseek": TEAL,
    "grok": VIOLET,
    "gemini": AMBER,
}

_GRAD = [TEAL, "#4fcdbf", "#6fd3cb", VIOLET]


def gradient(text: str, colors=_GRAD) -> Text:
    out = Text()
    n = max(len(text) - 1, 1)
    steps = len(colors) - 1
    for i, ch in enumerate(text):
        seg = min(int(i / n * steps), steps)
        out.append(ch, style=colors[seg])
    return out


def banner(subtitle: str = "drive AI chat UIs through a real browser") -> None:
    title = Text()
    title.append("▚ ", style=TEAL)
    title.append(gradient("AI BROWSER BRIDGE"))
    body = Text()
    body.append(subtitle, style=SOFT)
    panel = Panel(
        Align.center(Text.assemble(title, "\n", body)),
        border_style=TEAL,
        padding=(1, 4),
    )
    console.print(panel)


def ok(msg: str) -> None:
    console.print(f"[{GOOD}]✓[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"[{AMBER}]![/] {msg}")


def fail(msg: str) -> None:
    # escape() so a message like "[chatgpt] not authenticated" is not eaten as markup
    console.print(f"[{CORAL}]✗[/] {escape(msg)}")


def pstyle(name: str) -> str:
    return PROVIDER_STYLE.get(name, TEAL)


def chip(name: str) -> Text:
    return Text(f" {name} ", style=f"bold white on {pstyle(name)}")
