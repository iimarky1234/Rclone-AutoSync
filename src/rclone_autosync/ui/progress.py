"""Rich Live progress display manager for rasync sync execution."""

from __future__ import annotations

import time
from typing import Optional
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text

from ..models import SyncProfile, SyncRunResult, TransferProgress
from .formatters import format_bytes, format_duration, format_speed


class SyncProgressDisplay:
    """Manages rich real-time visual progress during sync operations."""

    def __init__(self, profile: SyncProfile, dry_run: bool = False, console: Optional[Console] = None):
        self.profile = profile
        self.dry_run = dry_run
        self.console = console or Console()
        self.live: Optional[Live] = None
        self.last_progress: Optional[TransferProgress] = None
        self.start_time = time.time()

    def __enter__(self) -> SyncProgressDisplay:
        self.live = Live(
            self._render_view(),
            console=self.console,
            refresh_per_second=8,
            transient=True,
        )
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.live:
            self.live.__exit__(exc_type, exc_val, exc_tb)

    def update(self, progress: TransferProgress) -> None:
        self.last_progress = progress
        if self.live:
            self.live.update(self._render_view())

    def _render_view(self) -> Panel:
        prog = self.last_progress
        dry_str = " [bold yellow][DRY-RUN][/bold yellow]" if self.dry_run else ""
        header_text = Text.from_markup(
            f"Syncing: [bold cyan]{self.profile.name}[/bold cyan] ({self.profile.mode.value.upper()}) "
            f"➜ [green]{self.profile.full_remote_target}[/green]{dry_str}"
        )

        elements = []

        if not prog or (prog.total_bytes == 0 and prog.bytes_transferred == 0):
            # Scanning / checking / diffing phase
            msg = prog.current_message if prog and prog.current_message else "Analyzing directories and remote hashes..."
            checks_count = f"{prog.checks} checked" if prog and prog.checks > 0 else ""
            elapsed = round(time.time() - self.start_time, 1)

            scan_table = Table.grid(padding=(0, 1))
            scan_table.add_row(
                Text("⠋", style="bold cyan"),
                Text("Scanning & Diffing:", style="bold white"),
                Text(msg[:80], style="dim cyan"),
                Text(checks_count, style="yellow"),
                Text(f"({elapsed}s)", style="dim"),
            )
            elements.append(scan_table)

        else:
            # Transferring phase
            pct = prog.percentage
            done_str = format_bytes(prog.bytes_transferred)
            total_str = format_bytes(prog.total_bytes)
            speed_str = format_speed(prog.speed)
            eta_str = format_duration(prog.eta_seconds)
            files_str = f"{prog.transfers}/{prog.total_transfers} files"

            # Progress bar simulation
            bar_width = 32
            filled = int(bar_width * (pct / 100.0))
            bar_visual = f"[bold green]{'━' * filled}[/bold green][dim white]{'━' * (bar_width - filled)}[/dim white]"

            stats_table = Table.grid(padding=(0, 2))
            stats_table.add_row(
                Text.from_markup(f"{bar_visual} [bold cyan]{pct:.1f}%[/bold cyan]"),
                Text.from_markup(f"[bold]{done_str}[/bold] / {total_str}"),
                Text.from_markup(f"Speed: [green]{speed_str}[/green]"),
                Text.from_markup(f"ETA: [yellow]{eta_str}[/yellow]"),
                Text.from_markup(f"Done: [magenta]{files_str}[/magenta]"),
            )
            elements.append(stats_table)

            # Active file transfers table
            if prog.active_files:
                file_table = Table(
                    show_header=True,
                    header_style="bold dim",
                    box=None,
                    padding=(0, 1),
                )
                file_table.add_column("Transferring File", style="cyan", no_wrap=True)
                file_table.add_column("Progress", style="green", justify="right")
                file_table.add_column("Speed", style="yellow", justify="right")
                file_table.add_column("Size", style="dim", justify="right")

                for f in prog.active_files[:4]:  # Show up to 4 concurrent files
                    file_table.add_row(
                        f.name[:45],
                        f"{f.percentage}%",
                        format_speed(f.speed),
                        format_bytes(f.size),
                    )
                elements.append(file_table)

            elif prog.current_message:
                elements.append(Text(f"  • {prog.current_message[:90]}", style="dim cyan"))

        return Panel(
            Group(*elements),
            title=header_text,
            border_style="cyan" if not self.dry_run else "yellow",
            padding=(1, 2),
        )

    def print_completion(self, result: SyncRunResult) -> None:
        """Print rich completion banner to terminal."""
        dry_tag = " (SIMULATION ONLY)" if result.dry_run else ""
        if result.success:
            title = f"[bold green]✔ Sync Completed Successfully{dry_tag}[/bold green]"
            border = "green"
        else:
            title = f"[bold red]✖ Sync Operation Failed[/bold red]"
            border = "red"

        summary_table = Table.grid(padding=(0, 3))
        summary_table.add_row(
            f"[bold]Profile:[/bold] {result.profile_name}",
            f"[bold]Transferred:[/bold] {result.files_transferred} files ({format_bytes(result.bytes_transferred)})",
            f"[bold]Duration:[/bold] {result.duration_seconds}s",
        )
        if result.files_deleted > 0 or result.files_checked > 0:
            summary_table.add_row(
                f"[bold]Mode:[/bold] {self.profile.mode.value.upper()}",
                f"[bold]Deleted/Trashed:[/bold] {result.files_deleted} files",
                f"[bold]Checked:[/bold] {result.files_checked} files",
            )

        items = [summary_table]
        if not result.success and result.error_message:
            items.append(Text(f"\nError: {result.error_message}", style="bold red"))

        self.console.print(Panel(Group(*items), title=title, border_style=border, padding=(1, 2)))
