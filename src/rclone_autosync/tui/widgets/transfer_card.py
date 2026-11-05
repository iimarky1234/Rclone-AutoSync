"""Live transfer card widget showing active rclone progress."""

from __future__ import annotations

from typing import Optional
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Label, ProgressBar, Static

from ...models import TransferProgress
from ..formatters import format_bytes, format_duration, format_speed


class TransferCard(Widget):
    """Widget displaying active file transfer progress and metrics."""

    def __init__(self, **kwargs):
        super().__init__(id="transfer-card", **kwargs)
        self.progress: Optional[TransferProgress] = None

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="card-header"):
                yield Label("Active Transfer: ", classes="card-title")
                yield Label("Idle", id="transfer-profile-name", classes="stat-value")
                yield Label("", id="transfer-dryrun-badge")

            yield ProgressBar(total=100, show_eta=False, id="transfer-progress-bar")

            with Horizontal(id="card-stats-row"):
                yield Label("Progress: ", classes="stat-label")
                yield Label("0 B / 0 B (0%)", id="transfer-bytes-label", classes="stat-value")
                yield Label("  |  Speed: ", classes="stat-label")
                yield Label("0 B/s", id="transfer-speed-label", classes="stat-value")
                yield Label("  |  ETA: ", classes="stat-label")
                yield Label("--:--", id="transfer-eta-label", classes="stat-value")
                yield Label("  |  Files: ", classes="stat-label")
                yield Label("0 / 0", id="transfer-files-label", classes="stat-value")

            yield Static("", id="active-files-display")

    def update_progress(self, progress: Optional[TransferProgress]) -> None:
        self.progress = progress
        name_label = self.query_one("#transfer-profile-name", Label)
        badge_label = self.query_one("#transfer-dryrun-badge", Label)
        bar = self.query_one("#transfer-progress-bar", ProgressBar)
        bytes_label = self.query_one("#transfer-bytes-label", Label)
        speed_label = self.query_one("#transfer-speed-label", Label)
        eta_label = self.query_one("#transfer-eta-label", Label)
        files_label = self.query_one("#transfer-files-label", Label)
        active_files_static = self.query_one("#active-files-display", Static)

        if not progress or progress.status != "syncing":
            name_label.update("No active sync in progress")
            badge_label.update("")
            bar.update(total=100, progress=0)
            bytes_label.update("-")
            speed_label.update("0 B/s")
            eta_label.update("--:--")
            files_label.update("-")
            active_files_static.update("")
            return

        name_label.update(progress.profile_name)
        if progress.dry_run:
            badge_label.update(" [DRY-RUN]")
        else:
            badge_label.update("")

        total_b = progress.total_bytes
        done_b = progress.bytes_transferred
        pct = progress.percentage

        if total_b > 0:
            bar.update(total=100, progress=min(100.0, pct))
            bytes_text = f"{format_bytes(done_b)} / {format_bytes(total_b)} ({pct:.1f}%)"
        elif done_b > 0:
            bar.update(total=None)
            bytes_text = f"{format_bytes(done_b)} transferred"
        else:
            # Indeterminate pulse while listing, diffing, and checking
            bar.update(total=None)
            bytes_text = "Scanning & Checking..."

        bytes_label.update(bytes_text)
        speed_label.update(format_speed(progress.speed))
        eta_label.update(format_duration(progress.eta_seconds))

        if progress.total_transfers > 0:
            files_label.update(f"{progress.transfers} / {progress.total_transfers}")
        elif progress.checks > 0:
            files_label.update(f"{progress.checks} checked")
        else:
            files_label.update("Listing...")

        if progress.active_files:
            file_lines = []
            for f in progress.active_files[:3]:  # show top 3 active files
                file_pct = f"{f.percentage}%" if f.percentage else ""
                speed_str = format_speed(f.speed) if f.speed else ""
                file_lines.append(f"  • {f.name}  [{file_pct} {speed_str}]")
            active_files_static.update("\n".join(file_lines))
        elif progress.current_message:
            active_files_static.update(f"  • {progress.current_message[:95]}")
        else:
            active_files_static.update("  • Initializing...")
