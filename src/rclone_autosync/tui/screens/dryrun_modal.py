"""Modal dialog displaying dry-run preview results."""

from __future__ import annotations

from typing import List, Optional
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RichLog

from ...models import SyncProfile, SyncRunResult
from ..formatters import format_bytes


class DryRunModal(ModalScreen[bool]):
    """Displays dry-run analysis and logs before live sync."""

    def __init__(
        self,
        profile: SyncProfile,
        result: SyncRunResult,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.profile = profile
        self.result = result

    def compose(self) -> ComposeResult:
        with Vertical(id="dryrun-dialog"):
            yield Label(
                f"Dry-Run Preview: {self.profile.name}",
                id="modal-title",
            )

            status_style = "bold #10b981" if self.result.success else "bold #ef4444"
            status_text = "SUCCESS (Simulation Only)" if self.result.success else "FAILED"

            with Horizontal(id="dryrun-stats-summary"):
                yield Label(f"Status: [{status_text}]  |  ", classes="stat-label")
                yield Label(
                    f"To Transfer: {self.result.files_transferred} ({format_bytes(self.result.bytes_transferred)})  |  "
                    f"To Delete: {self.result.files_deleted}  |  "
                    f"Duration: {self.result.duration_seconds}s",
                    classes="stat-value",
                )

            yield RichLog(id="dryrun-log", highlight=True, markup=True)

            with Horizontal(id="modal-buttons"):
                yield Button("Close Preview", variant="default", id="btn-close")
                yield Button(
                    "Execute Actual Sync Now",
                    variant="success",
                    classes="success",
                    id="btn-execute",
                )

    def on_mount(self) -> None:
        log_widget = self.query_one("#dryrun-log", RichLog)
        if self.result.action_log:
            for line in self.result.action_log:
                if "error" in line.lower():
                    log_widget.write(Text(line, style="bold red"))
                elif "notice" in line.lower() or "skip" in line.lower():
                    log_widget.write(Text(line, style="yellow"))
                else:
                    log_widget.write(Text(line, style="dim"))
        else:
            log_widget.write(Text("No changes detected. Everything is already in sync!", style="green"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-execute":
            self.dismiss(True)  # Return True to indicate user wants to execute actual sync
        else:
            self.dismiss(False)
