"""Daemon service control and live log console."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Label, RichLog

from ...daemon.ipc import DaemonClient
from ...daemon.systemd import (
    get_recent_journal_logs,
    get_service_status_summary,
    install_service,
    is_service_active,
    is_service_enabled,
    is_service_installed,
    restart_service,
    start_service,
    stop_service,
)


class DaemonTab(Widget):
    """Tab for managing systemd user service and viewing logs."""

    def __init__(self, client: DaemonClient, **kwargs):
        super().__init__(id="daemon-container", **kwargs)
        self.client = client

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="daemon-status-box"):
                with Horizontal():
                    yield Label("systemd Service: ", classes="stat-label")
                    yield Label("Checking...", id="daemon-service-status-lbl", classes="stat-value")
                    yield Label("  |  Socket: ", classes="stat-label")
                    yield Label("Checking...", id="daemon-socket-status-lbl", classes="stat-value")

            with Horizontal(id="daemon-actions"):
                yield Button("Start Service", variant="success", classes="success", id="btn-start-srv")
                yield Button("Stop Service", variant="error", classes="danger", id="btn-stop-srv")
                yield Button("Restart", variant="default", id="btn-restart-srv")
                yield Button("Install Service", variant="primary", classes="primary", id="btn-install-srv")
                yield Button("Reload Logs", variant="default", id="btn-reload-logs")

            yield Label("Live Service / Rclone Logs:", classes="pane-title")
            yield RichLog(id="daemon-log-view", highlight=True, markup=True)

    async def on_mount(self) -> None:
        await self.refresh_service_status()
        await self.load_journal_logs()

    async def refresh_service_status(self) -> None:
        status_lbl = self.query_one("#daemon-service-status-lbl", Label)
        socket_lbl = self.query_one("#daemon-socket-status-lbl", Label)
        install_btn = self.query_one("#btn-install-srv", Button)

        installed = await is_service_installed()
        summary = await get_service_status_summary()
        socket_alive = await self.client.is_running()

        if installed:
            install_btn.label = "Reinstall Service"
        else:
            install_btn.label = "Install Service"

        status_style = "bold #10b981" if "running" in summary else "bold #ef4444"
        status_lbl.update(Text(summary.upper(), style=status_style))

        sock_style = "bold #10b981" if socket_alive else "bold #94a3b8"
        sock_text = "CONNECTED" if socket_alive else "DISCONNECTED"
        socket_lbl.update(Text(sock_text, style=sock_style))

    async def load_journal_logs(self) -> None:
        log_widget = self.query_one("#daemon-log-view", RichLog)
        logs = await get_recent_journal_logs(lines=40)
        log_widget.clear()
        if logs.strip():
            for line in logs.splitlines():
                if "error" in line.lower():
                    log_widget.write(Text(line, style="bold red"))
                elif "warn" in line.lower():
                    log_widget.write(Text(line, style="yellow"))
                elif "info" in line.lower():
                    log_widget.write(Text(line, style="#38bdf8"))
                else:
                    log_widget.write(Text(line, style="dim"))
        else:
            log_widget.write(Text("No service logs found yet.", style="dim"))

    def write_live_log(self, level: str, message: str) -> None:
        """Called when an IPC live log event is broadcasted."""
        try:
            log_widget = self.query_one("#daemon-log-view", RichLog)
            style = "bold red" if level == "error" else "yellow" if level == "warning" else "#38bdf8"
            log_widget.write(Text(f"[{level.upper()}] {message}", style=style))
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-start-srv":
            ok, msg = await start_service()
            self.app.notify(msg, severity="information" if ok else "error")
        elif btn_id == "btn-stop-srv":
            ok, msg = await stop_service()
            self.app.notify(msg, severity="information" if ok else "error")
        elif btn_id == "btn-restart-srv":
            ok, msg = await restart_service()
            self.app.notify(msg, severity="information" if ok else "error")
        elif btn_id == "btn-install-srv":
            ok, msg = await install_service(start_now=True)
            self.app.notify(msg, severity="information" if ok else "error")
        elif btn_id == "btn-reload-logs":
            await self.load_journal_logs()

        await self.refresh_service_status()
