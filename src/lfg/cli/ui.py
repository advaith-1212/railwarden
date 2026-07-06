from __future__ import annotations

import getpass
import os
import sys
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lfg.errors import LfgError

console = Console(highlight=False)


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("LFG_PLAIN_UI"):
        return False
    return console.is_terminal


def use_interactive_select() -> bool:
    if os.environ.get("LFG_PLAIN_PROMPTS"):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if not sys.stdin.isatty():
        return False
    return color_enabled()


def _questionary() -> tuple[Any, Any]:
    import questionary
    from questionary import Style

    return questionary, Style(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "bold fg:white"),
            ("answer", "fg:green bold"),
            ("pointer", "fg:yellow bold"),
            ("highlighted", "fg:black bg:yellow bold"),
            ("selected", "fg:green"),
            ("text", "fg:white"),
            ("instruction", "fg:bright_black italic"),
            ("separator", "fg:bright_black"),
        ]
    )


def banner(title: str, *, subtitle: str = "") -> None:
    body = Text(title, style="bold white")
    if subtitle:
        body.append("\n")
        body.append(subtitle, style="dim")
    console.print(Panel(body, border_style="cyan", box=box.ROUNDED))


def section(title: str, *, hint: str = "") -> None:
    console.print()
    console.rule(f"[bold cyan]{title}[/]")
    if hint:
        console.print(f"[dim]{hint}[/]")


def wizard_step(step: int, total: int, title: str, *, hint: str = "") -> None:
    console.print()
    console.rule(
        f"[bold cyan]Step {step}/{total}[/]  [bold white]{title}[/]",
        style="bright_black",
    )
    if hint:
        console.print(f"[dim italic]{hint}[/]")


def role_card(role: str, detail: str) -> None:
    console.print(
        Panel(
            detail,
            title=f"[bold yellow]{role}[/]",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def info(message: str) -> None:
    console.print(f"[dim]→[/] {message}")


def success(message: str) -> None:
    console.print(f"[bold green]✓[/] {message}")


def warning(message: str) -> None:
    console.print(f"[bold yellow]![/] {message}")


def error(message: str) -> None:
    console.print(f"[bold red]✗[/] {message}")


def prompt_text(default: str, label: str, *, hint: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    if hint:
        console.print(f"[dim]{hint}[/]")
    if use_interactive_select():
        questionary, style = _questionary()
        value = questionary.text(
            label,
            default=default,
            style=style,
        ).ask()
        if value is None:
            raise LfgError("Setup cancelled")
        stripped = str(value).strip()
        return stripped or default
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def prompt_secret(label: str, *, hint: str = "") -> str:
    if not sys.stdin.isatty():
        return ""
    if hint:
        console.print(f"[dim]{hint}[/]")
    if use_interactive_select():
        questionary, style = _questionary()
        value = questionary.password(label, style=style).ask()
        if value is None:
            raise LfgError("Setup cancelled")
        return str(value).strip()
    return getpass.getpass(f"{label}: ").strip()


def prompt_choice(default: str, label: str, choices: dict[str, str]) -> str:
    if not sys.stdin.isatty():
        return default
    if use_interactive_select():
        questionary, style = _questionary()
        keys = list(choices.keys())
        default_index = keys.index(default) if default in keys else 0
        from questionary import Choice

        value = questionary.select(
            label,
            choices=[
                Choice(value=key, title=f"{key} — {choices[key]}") for key in keys
            ],
            default=keys[default_index],
            instruction="(↑↓ move, Enter to select)",
            style=style,
            use_indicator=True,
            use_shortcuts=len(keys) <= 9,
        ).ask()
        if value is None:
            raise LfgError("Setup cancelled")
        return str(value)
    console.print(f"[bold]{label}[/]")
    keys = list(choices.keys())
    for index, key in enumerate(keys, start=1):
        marker = " [green](default)[/]" if key == default else ""
        console.print(
            f"  [cyan]{index}.[/] [bold]{key}[/]{marker}  [dim]{choices[key]}[/]"
        )
    value = input(f"Choose number or key [{default}]: ").strip()
    if not value:
        return default
    if value.isdigit():
        pick = int(value)
        if 1 <= pick <= len(keys):
            return keys[pick - 1]
    if value in choices:
        return value
    raise LfgError(f"Expected one of: {', '.join(choices)}")


def prompt_bool(default: bool, label: str) -> bool:
    if not sys.stdin.isatty():
        return default
    if use_interactive_select():
        questionary, style = _questionary()
        value = questionary.confirm(label, default=default, style=style).ask()
        if value is None:
            raise LfgError("Setup cancelled")
        return bool(value)
    value = prompt_text("yes" if default else "no", label).lower()
    if value in {"1", "true", "yes", "y"}:
        return True
    if value in {"0", "false", "no", "n"}:
        return False
    raise LfgError(f"Expected yes/no for {label}")


def prompt_float(default: float, label: str) -> float:
    return float(prompt_text(str(default), label))


def prompt_optional_int(default: int | None, label: str) -> int | None:
    value = prompt_text("" if default is None else str(default), label)
    return int(value) if value else None


def print_table(headers: list[str], rows: list[list[object]]) -> None:
    if not rows:
        console.print("  [dim]none[/]")
        return
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*[_cell(item) for item in row])
    console.print(table)


def status_cell(value: object) -> str:
    text = str(value)
    if text in {"healthy", "available", "ok", "reachable"}:
        return f"[green]●[/] {text}"
    if text in {"missing", "failed", "unavailable", "unreachable"}:
        return f"[red]●[/] {text}"
    if text in {"skipped", "external-or-not-required", "external"}:
        return f"[dim]○[/] {text}"
    return text


def _cell(value: object) -> str:
    if value is None:
        return "[dim]-[/]"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def launch_summary(*, session: str, profile_path: str, hermes_home: str, attach_hint: str) -> None:
    table = Table(box=box.ROUNDED, border_style="green", show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Tmux session", session)
    table.add_row("Session profile", profile_path)
    table.add_row("Hermes home", hermes_home)
    console.print()
    console.print(Panel(table, title="[bold green]Factory launched[/]", border_style="green"))
    console.print()
    console.print("[bold]Windows[/]")
    console.print("  [cyan]factory[/]        Hermes (left) + workers (right)")
    console.print("  [cyan]observability[/]  Controller + live dashboard")
    if attach_hint:
        console.print()
        console.print(f"[dim]{attach_hint}[/]")


def setup_summary_block(*, repository: str, configured: bool, created: bool) -> None:
    banner("LFG setup", subtitle=f"Repository: {repository}")
    if created:
        success("Created .lfg project configuration")
    elif configured:
        info("Project configuration already exists")
    console.print()
    section("Next steps", hint="Run these before your first factory launch")
    console.print("  [cyan]1.[/] [bold]lfg doctor[/]     [dim]verify tools, credentials, MCP[/]")
    console.print("  [cyan]2.[/] [bold]lfg context status[/]  [dim]populate context if needed[/]")
    console.print("  [cyan]3.[/] [bold]lfg launch[/]     [dim]start the factory (guided wizard)[/]")


def key_value_panel(title: str, rows: dict[str, Any]) -> None:
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column(style="cyan")
    table.add_column(style="white")
    for key, value in rows.items():
        table.add_row(key, str(value))
    console.print(Panel(table, title=f"[bold]{title}[/]", border_style="cyan"))