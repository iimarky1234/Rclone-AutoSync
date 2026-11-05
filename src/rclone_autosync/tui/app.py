"""Main Textual application for rclone-autosync."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from ..daemon.ipc import DaemonClient
from ..models import TransferProgress
from .screens.daemon_view import DaemonTab
from .screens.dashboard import DashboardTab
from .screens.explorer import ExplorerTab
from .widgets.transfer_card import TransferCard


class RcloneAutoSyncApp(App):
    """Modern TUI Application for Rclone AutoSync."""

    CSS_PATH = "styles.tcss"
    TITLE = "Rclone AutoSync"
    SUB_TITLE = "Local ⇆ Cloud File Synchronizer"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("1", "switch_tab('tab-dashboard')", "Dashboard", show=False),
        Binding("2", "switch_tab('tab-explorer')", "Drive Explorer", show=False),
        Binding("3", "switch_tab('tab-daemon')", "Daemon & Logs", show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = DaemonClient()
        self._listener_task: Optional[asyncio.Task] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-dashboard"):
            with TabPane("📊 Dashboard", id="tab-dashboard"):
                yield DashboardTab(client=self.client)
            with TabPane("📁 Drive & Local Explorer", id="tab-explorer"):
                yield ExplorerTab()
            with TabPane("⚙️ Daemon & Logs", id="tab-daemon"):
                yield DaemonTab(client=self.client)
        yield Footer()

    async def on_mount(self) -> None:
        self._listener_task = asyncio.create_task(self._listen_daemon_events())

    async def on_unmount(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()

    def action_switch_tab(self, tab_id: str) -> None:
        tabbed_content = self.query_one(TabbedContent)
        tabbed_content.active = tab_id

    async def _listen_daemon_events(self) -> None:
        """Background task that connects to IPC socket and receives pushed daemon events."""
        while True:
            try:
                if await self.client.is_running():
                    async for event in self.client.subscribe_events():
                        evt_type = event.get("event")
                        data = event.get("data")

                        if evt_type == "progress":
                            prog = TransferProgress.model_validate(data)
                            try:
                                card = self.query_one(TransferCard)
                                card.update_progress(prog)
                            except Exception:
                                pass

                        elif evt_type == "status_change":
                            try:
                                dash = self.query_one(DashboardTab)
                                await dash.refresh_dashboard()
                            except Exception:
                                pass

                        elif evt_type == "sync_completed":
                            try:
                                dash = self.query_one(DashboardTab)
                                await dash.refresh_dashboard()
                                card = self.query_one(TransferCard)
                                card.update_progress(None)
                            except Exception:
                                pass

                        elif evt_type == "log":
                            try:
                                daemon_tab = self.query_one(DaemonTab)
                                daemon_tab.write_live_log(
                                    data.get("level", "info"),
                                    data.get("message", ""),
                                )
                            except Exception:
                                pass

                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2.0)
