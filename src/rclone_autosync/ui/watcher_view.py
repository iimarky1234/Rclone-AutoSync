"""Foreground interactive watcher for terminal sessions."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..models import SyncProfile
from ..rclone.executor import RcloneExecutor
from .formatters import format_bytes
from .progress import SyncProgressDisplay

console = Console()


class ForegroundWatcherHandler(FileSystemEventHandler):
    """File event handler for foreground watching."""

    def __init__(
        self,
        profile: SyncProfile,
        loop: asyncio.AbstractEventLoop,
        on_trigger: callable,
    ):
        super().__init__()
        self.profile = profile
        self.loop = loop
        self.on_trigger = on_trigger
        self.root_path = profile.resolved_local_path
        self._timer_handle: Optional[asyncio.TimerHandle] = None
        self._is_syncing = False

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or self._is_syncing:
            return

        try:
            event_path = Path(event.src_path).resolve()
            rel_path = str(event_path.relative_to(self.root_path))
        except Exception:
            rel_path = event.src_path

        from ..daemon.watcher import should_ignore_path
        if should_ignore_path(rel_path, self.profile.excludes):
            return

        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim]{now_str}[/dim] [bold cyan]CHANGE[/bold cyan]   {rel_path} [dim]({event.event_type})[/dim]")

        self.loop.call_soon_threadsafe(self._reset_debounce)

    def _reset_debounce(self) -> None:
        if self._timer_handle:
            self._timer_handle.cancel()

        delay = max(2, self.profile.debounce_seconds)
        self._timer_handle = self.loop.call_later(
            delay,
            self._on_debounce_complete,
        )

    def _on_debounce_complete(self) -> None:
        self._timer_handle = None
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim]{now_str}[/dim] [bold yellow]DEBOUNCE[/bold yellow] Quiet period elapsed. Triggering sync...")
        self.loop.create_task(self._execute_sync())

    async def _execute_sync(self) -> None:
        self._is_syncing = True
        try:
            with SyncProgressDisplay(self.profile, console=console) as display:
                executor = RcloneExecutor(self.profile)
                result = await executor.run(on_progress=display.update)
                display.print_completion(result)
        finally:
            self._is_syncing = False


async def run_foreground_watcher(profile: SyncProfile) -> None:
    """Run interactive foreground file watcher for a profile."""
    local_path = profile.resolved_local_path
    if not local_path.exists():
        console.print(f"[bold red]Error:[/bold red] Local directory '{local_path}' does not exist.")
        return

    banner = (
        f"[bold cyan]👀 Foreground File Watcher[/bold cyan]\n"
        f"Profile: [bold white]{profile.name}[/bold white]\n"
        f"Watching: [green]{local_path}[/green]\n"
        f"Debounce: [yellow]{profile.debounce_seconds}s[/yellow] | Target: [cyan]{profile.full_remote_target}[/cyan]\n"
        f"[dim]Listening for file changes... Press Ctrl+C to exit.[/dim]"
    )
    console.print(Panel(banner, border_style="cyan"))

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    handler = ForegroundWatcherHandler(profile=profile, loop=loop, on_trigger=None)
    observer = Observer()
    observer.schedule(handler, str(local_path), recursive=True)
    observer.start()

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping file watcher...[/yellow]")
    finally:
        observer.stop()
        observer.join(timeout=2.0)
        console.print("[dim]Watcher stopped.[/dim]\n")
