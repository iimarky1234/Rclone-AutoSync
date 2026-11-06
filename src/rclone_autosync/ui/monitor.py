"""Live terminal status bar and dashboard monitor for the rasync daemon."""

from __future__ import annotations

import asyncio
import collections
import shutil
import sys
import time
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import load_profiles
from ..daemon.ipc import DaemonClient
from ..daemon.systemd import get_service_status_summary, is_service_active, start_service
from ..models import ActiveFileTransfer, ProfileStatus, SyncProfile, TransferProgress
from .formatters import format_bytes, format_duration, format_iso_timestamp, format_speed
from .progress import render_task_bar

console = Console()


class DaemonLiveMonitor:
    """Continuous terminal monitor and task bar for background sync operations."""

    def __init__(self, client: Optional[DaemonClient] = None, refresh_per_second: float = 4.0):
        self.client = client or DaemonClient()
        self.refresh_rate = refresh_per_second
        self.console = Console()

        self.profiles: Dict[str, Dict[str, Any]] = {}
        self.statuses: Dict[str, str] = {}
        self.current_sync_profile_id: Optional[str] = None
        self.current_progress: Optional[TransferProgress] = None

        self.uptime_seconds: float = 0.0
        self.power_status: str = "Checking power..."
        self.is_ac_connected: bool = True
        self.has_battery: bool = False
        self.queued_count: int = 0

        self.recent_events: Deque[Tuple[str, str, str]] = collections.deque(maxlen=6)  # (time, level, message)
        self.start_wall_time = time.time()
        self.daemon_start_time = time.time()
        self._running = False
        self._sub_task: Optional[asyncio.Task] = None

    def _add_event(self, level: str, message: str) -> None:
        now_str = datetime.now().strftime("%H:%M:%S")
        self.recent_events.append((now_str, level.upper(), message))

    async def initialize_state(self) -> bool:
        """Fetch initial daemon state snapshot."""
        try:
            resp = await self.client.send_command("get_state")
            if resp.get("status") == "ok":
                data = resp.get("data", {})
                self.uptime_seconds = float(data.get("uptime_seconds", 0.0))
                self.statuses = data.get("statuses", {})
                self.current_sync_profile_id = data.get("current_sync_profile_id")
                self.power_status = data.get("power_status", "Unknown")
                self.is_ac_connected = bool(data.get("is_ac_connected", True))
                self.has_battery = bool(data.get("has_battery", False))
                self.queued_count = int(data.get("queued_count", 0))

                raw_prog = data.get("current_progress")
                if raw_prog:
                    self.current_progress = TransferProgress.model_validate(raw_prog)

                for p_dict in data.get("profiles", []):
                    self.profiles[p_dict["id"]] = p_dict

                self._add_event("info", f"Connected to rasync daemon ({len(self.profiles)} profiles)")
                return True
        except Exception as e:
            self._add_event("error", f"Failed to initialize daemon state: {e}")
        return False

    async def _event_listener(self) -> None:
        """Background task reading streamed events from daemon."""
        while self._running:
            try:
                async for event in self.client.subscribe_events():
                    if not self._running:
                        break
                    event_type = event.get("event")
                    data = event.get("data", {})

                    if event_type == "progress":
                        try:
                            self.current_progress = TransferProgress.model_validate(data)
                            self.current_sync_profile_id = self.current_progress.profile_id
                        except Exception:
                            pass

                    elif event_type == "status_change":
                        p_id = data.get("profile_id")
                        st = data.get("status")
                        if p_id and st:
                            self.statuses[p_id] = st
                            if st != ProfileStatus.SYNCING.value and p_id == self.current_sync_profile_id:
                                self.current_sync_profile_id = None
                                self.current_progress = None

                    elif event_type == "sync_completed":
                        p_name = data.get("profile_name", "Profile")
                        success = data.get("success", False)
                        files_done = data.get("files_transferred", 0)
                        bytes_done = data.get("bytes_transferred", 0)
                        duration = data.get("duration_seconds", 0)

                        if success:
                            msg = f"{p_name}: synced {files_done} files ({format_bytes(bytes_done)}) in {duration}s"
                            self._add_event("success", msg)
                        else:
                            err = data.get("error_message") or "Sync failed"
                            self._add_event("error", f"{p_name}: {err}")

                        self.current_sync_profile_id = None
                        self.current_progress = None

                    elif event_type == "log":
                        lvl = data.get("level", "info")
                        m = data.get("message", "")
                        if m:
                            self._add_event(lvl, m[:90])

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._add_event("warn", f"Daemon connection dropped: {e}. Reconnecting...")
                await asyncio.sleep(2.0)

    def _render_header(self) -> Panel:
        """Top banner with daemon uptime, power status, and overall health."""
        elapsed = round(self.uptime_seconds + (time.time() - self.start_wall_time))
        uptime_str = format_duration(elapsed)

        power_icon = "🔌" if self.is_ac_connected else "🔋"
        power_color = "green" if self.is_ac_connected else "yellow"

        header_table = Table.grid(expand=True)
        header_table.add_column("left", justify="left")
        header_table.add_column("right", justify="right")

        left_text = (
            f"[bold cyan]⚡ Rclone AutoSync Monitor[/bold cyan] [dim]•[/dim] "
            f"Daemon: [bold green]ONLINE[/bold green] [dim](uptime {uptime_str})[/dim]"
        )
        right_text = f"{power_icon} [{power_color}]{self.power_status}[/{power_color}]"

        header_table.add_row(Text.from_markup(left_text), Text.from_markup(right_text))
        return Panel(header_table, border_style="dim cyan", padding=(0, 1))

    def _render_task_bar_panel(self) -> Panel:
        """Render the prominent sync progress task bar panel."""
        elements = []

        if self.current_progress and self.current_progress.status == "syncing":
            prog = self.current_progress
            p_dict = self.profiles.get(prog.profile_id, {})
            mode_str = p_dict.get("mode", "SYNC").upper()
            dry_badge = " [bold yellow][DRY-RUN][/bold yellow]" if prog.dry_run else ""

            # Header row
            header_text = Text.from_markup(
                f"[bold cyan]🔄 Syncing:[/bold cyan] [bold white]{prog.profile_name}[/bold white] "
                f"[dim]({mode_str})[/dim] ➜ [green]{p_dict.get('remote', '')}:{p_dict.get('remote_path', '')}[/green]{dry_badge}"
            )
            elements.append(header_text)
            elements.append(Text(""))

            if prog.total_bytes == 0 and prog.bytes_transferred == 0:
                # Scanning / diffing phase
                msg = prog.current_message or "Scanning directories and computing remote hashes..."
                checks_str = f" [yellow]{prog.checks} checked[/yellow]" if prog.checks > 0 else ""
                elements.append(
                    Text.from_markup(
                        f"  [bold yellow]⠋ Scanning & Diffing:[/bold yellow] [dim cyan]{msg[:75]}[/dim cyan]{checks_str}"
                    )
                )
            else:
                # Active transfer overall task bar
                pct = prog.percentage
                bar = render_task_bar(pct, width=32, fill_style="bold green")
                done_str = format_bytes(prog.bytes_transferred)
                tot_str = format_bytes(prog.total_bytes)
                speed_str = format_speed(prog.speed)
                eta_str = format_duration(prog.eta_seconds)
                done_files = f"{prog.transfers}/{prog.total_transfers} files"

                taskbar_grid = Table.grid(padding=(0, 2))
                taskbar_grid.add_row(
                    Text.from_markup(f"  {bar} [bold cyan]{pct:>5.1f}%[/bold cyan]"),
                    Text.from_markup(f"[bold]{done_str}[/bold] / {tot_str}"),
                    Text.from_markup(f"Speed: [green]{speed_str}[/green]"),
                    Text.from_markup(f"ETA: [yellow]{eta_str}[/yellow]"),
                    Text.from_markup(f"Files: [magenta]{done_files}[/magenta]"),
                )
                elements.append(taskbar_grid)

            # Concurrent file transfers
            if prog.active_files:
                elements.append(Text(""))
                file_table = Table(
                    show_header=True,
                    header_style="bold dim",
                    box=None,
                    padding=(0, 1),
                )
                file_table.add_column("Transferring File Task", style="cyan", no_wrap=True)
                file_table.add_column("Task Progress Bar", justify="left")
                file_table.add_column("Speed", style="yellow", justify="right")
                file_table.add_column("Size", style="dim", justify="right")

                for f in prog.active_files[:4]:
                    f_bar = render_task_bar(f.percentage, width=14, fill_style="bold cyan")
                    file_table.add_row(
                        f"  {f.name[:35]}",
                        Text.from_markup(f"{f_bar} [bold green]{f.percentage:>3}%[/bold green]"),
                        format_speed(f.speed),
                        format_bytes(f.size),
                    )
                elements.append(file_table)

            title = "[bold cyan]Active Synchronization Progress[/bold cyan]"
            border_style = "cyan"

        else:
            # Idle / watching state
            active_watches = sum(1 for p in self.profiles.values() if p.get("watch_enabled") and p.get("enabled"))
            status_summary = (
                f"[bold green]● ALL PROFILES IDLE & SYNCED[/bold green]\n"
                f"[dim]Continuous file watchers active on {active_watches} profile(s). "
                f"Changes will automatically trigger debounce and sync.[/dim]"
            )
            if not self.is_ac_connected and self.has_battery:
                status_summary += "\n[yellow]⚡ Running on battery: profiles with 'pause_on_battery' are paused.[/yellow]"

            elements.append(Text.from_markup(status_summary))
            title = "[bold green]System Status[/bold green]"
            border_style = "green"

        return Panel(Group(*elements), title=title, border_style=border_style, padding=(1, 2))

    def _render_profiles_table(self) -> Table:
        """Render table of all configured profiles and their current live states."""
        table = Table(
            box=None,
            show_header=True,
            header_style="bold cyan",
            padding=(0, 2),
            expand=True,
        )
        table.add_column("Profile", style="bold white")
        table.add_column("Mode", justify="center")
        table.add_column("Local Path", style="dim")
        table.add_column("Target Remote", style="green")
        table.add_column("Status", justify="center")
        table.add_column("Last Sync", style="dim")

        for pid, p in self.profiles.items():
            status_val = self.statuses.get(pid, ProfileStatus.IDLE.value)

            if status_val == ProfileStatus.SYNCING.value:
                badge = "[bold cyan]SYNCING 🔄[/bold cyan]"
            elif status_val == ProfileStatus.QUEUED.value:
                badge = "[bold yellow]QUEUED ⏳[/bold yellow]"
            elif status_val == ProfileStatus.WATCHING.value:
                badge = "[green]WATCHING[/green]"
            elif status_val == ProfileStatus.ERROR.value:
                badge = "[bold red]ERROR ✖[/bold red]"
            elif status_val == ProfileStatus.DISABLED.value:
                badge = "[dim]DISABLED[/dim]"
            else:
                badge = "[dim]IDLE[/dim]"

            # Check if paused on battery
            if p.get("pause_on_battery") and not self.is_ac_connected and self.has_battery:
                badge += " [yellow](Paused)[/yellow]"

            last_time = format_iso_timestamp(p.get("last_sync_time"))
            remote_target = f"{p.get('remote', '')}:{p.get('remote_path', '')}"

            table.add_row(
                p.get("name", pid),
                p.get("mode", "sync").upper(),
                p.get("local_path", ""),
                remote_target,
                badge,
                last_time,
            )

        return table

    def _render_recent_events(self) -> Panel:
        """Render recent activity log feed."""
        if not self.recent_events:
            content = Text("No events recorded yet.", style="dim")
        else:
            lines = []
            for ev_time, ev_lvl, ev_msg in self.recent_events:
                lvl_color = "green" if ev_lvl == "SUCCESS" else "red" if ev_lvl == "ERROR" else "cyan" if ev_lvl == "NOTICE" else "dim"
                lines.append(Text.from_markup(f"[dim]{ev_time}[/dim] [{lvl_color}]{ev_lvl:>7}[/{lvl_color}]  {ev_msg}"))
            content = Group(*lines)

        return Panel(content, title="[bold]Recent Daemon Events[/bold]", border_style="dim", padding=(0, 2))

    def _render_view(self) -> Group:
        """Compose entire live monitor view."""
        header = self._render_header()
        taskbar_panel = self._render_task_bar_panel()
        profiles_panel = Panel(self._render_profiles_table(), title="[bold]Configured Sync Profiles[/bold]", border_style="dim", padding=(0, 1))
        events_panel = self._render_recent_events()

        # Bottom help footer
        footer = Text.from_markup(
            "[dim]Press [/dim][bold cyan]Ctrl+C[/bold cyan][dim] to exit monitor  •  "
            "Trigger manual sync in another terminal: [/dim][bold]rasync sync <name>[/bold]"
        )

        return Group(
            header,
            taskbar_panel,
            profiles_panel,
            events_panel,
            footer,
        )

    async def run(self, once: bool = False) -> int:
        """Run the live terminal monitor loop."""
        # 1. Verify daemon is online
        if not await self.client.is_running():
            console.print("\n[bold yellow]⚠️  The rasync background daemon is currently offline.[/bold yellow]")
            svc_active = await is_service_active()
            if not svc_active:
                summary = await get_service_status_summary()
                console.print(f"[dim]systemd User Service is {summary}.[/dim]\n")
                console.print("To start the daemon in the background:")
                console.print("  [bold cyan]rasync service start[/bold cyan]\n")
                console.print("To run a one-time sync directly:")
                console.print("  [bold cyan]rasync sync --all[/bold cyan]\n")
            return 1

        # 2. Fetch initial state
        await self.initialize_state()

        if once:
            self.console.print(self._render_view())
            return 0

        self._running = True
        self._sub_task = asyncio.create_task(self._event_listener())

        try:
            with Live(self._render_view(), console=self.console, refresh_per_second=self.refresh_rate, screen=True) as live:
                while self._running:
                    await asyncio.sleep(1.0 / self.refresh_rate)
                    live.update(self._render_view())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            if self._sub_task:
                self._sub_task.cancel()
                try:
                    await self._sub_task
                except Exception:
                    pass

        console.print("[dim]Monitor exited.[/dim]\n")
        return 0


async def run_live_monitor(once: bool = False) -> int:
    """Entry point for 'rasync monitor'."""
    monitor = DaemonLiveMonitor()
    return await monitor.run(once=once)
