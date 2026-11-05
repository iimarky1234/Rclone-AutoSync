"""Visual status pill widget."""

from __future__ import annotations

from textual.app import RenderResult
from textual.widget import Widget
from rich.text import Text

from ...models import ProfileStatus


class StatusPill(Widget):
    """Renders a colorful status pill for a profile status."""

    DEFAULT_CSS = """
    StatusPill {
        width: auto;
        height: 1;
    }
    """

    def __init__(self, status: ProfileStatus | str = ProfileStatus.IDLE, **kwargs):
        super().__init__(**kwargs)
        if isinstance(status, str):
            try:
                self.status = ProfileStatus(status.lower())
            except ValueError:
                self.status = ProfileStatus.IDLE
        else:
            self.status = status

    def update_status(self, status: ProfileStatus | str) -> None:
        if isinstance(status, str):
            try:
                self.status = ProfileStatus(status.lower())
            except ValueError:
                self.status = ProfileStatus.IDLE
        else:
            self.status = status
        self.refresh()

    def render(self) -> RenderResult:
        if self.status == ProfileStatus.WATCHING:
            return Text("● WATCHING", style="bold #10b981")
        elif self.status == ProfileStatus.SYNCING:
            return Text("⚡ SYNCING", style="bold #06b6d4")
        elif self.status == ProfileStatus.QUEUED:
            return Text("⏳ QUEUED", style="bold #f59e0b")
        elif self.status == ProfileStatus.ERROR:
            return Text("✖ ERROR", style="bold #ef4444")
        elif self.status == ProfileStatus.DISABLED:
            return Text("⊘ DISABLED", style="bold #64748b")
        else:
            return Text("○ IDLE", style="bold #94a3b8")
