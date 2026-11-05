"""Command-line interface entrypoints for rasync and rclone-autosync."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from rich.console import Console
from rich.table import Table

from .config import get_profile_by_id, get_profile_by_name, load_history, load_profiles
from .daemon.ipc import DaemonClient
from .daemon.service import run_daemon
from .daemon.systemd import (
    get_recent_journal_logs,
    get_service_status_summary,
    install_service,
    is_service_active,
    restart_service,
    start_service,
    stop_service,
    uninstall_service,
)
from .models import SyncProfile
from .rclone.executor import RcloneExecutor
from .tui.formatters import format_bytes, format_iso_timestamp

console = Console()


def run_tui() -> None:
    """Launch the interactive Textual TUI."""
    from .tui.app import RcloneAutoSyncApp
    app = RcloneAutoSyncApp()
    app.run()


async def cmd_sync(profile_query: str, dry_run: bool = False, resync: bool = False) -> int:
    """Trigger sync for a profile by name or ID."""
    profile = get_profile_by_id(profile_query) or get_profile_by_name(profile_query)
    if not profile:
        console.print(f"[bold red]Error:[/bold red] Profile '{profile_query}' not found.")
        return 1

    client = DaemonClient()
    if await client.is_running():
        console.print(f"[cyan]Daemon is running.[/cyan] Queuing sync for [bold]{profile.name}[/bold]...")
        try:
            resp = await client.send_command(
                "sync_now",
                profile_id=profile.id,
                dry_run=dry_run,
                resync=resync,
            )
            console.print(f"[green]Successfully queued:[/green] {resp}")
            return 0
        except Exception as e:
            console.print(f"[yellow]Daemon IPC error: {e}. Falling back to direct run.[/yellow]")

    console.print(f"Running sync directly for [bold]{profile.name}[/bold] (dry_run={dry_run}, resync={resync})...")
    executor = RcloneExecutor(profile=profile)
    result = await executor.run(dry_run=dry_run, resync=resync)

    if result.success:
        console.print(
            f"[bold green]Sync finished successfully![/bold green] "
            f"Transferred: {result.files_transferred} files ({format_bytes(result.bytes_transferred)}) "
            f"in {result.duration_seconds}s"
        )
        return 0
    else:
        console.print(f"[bold red]Sync failed:[/bold red] {result.error_message}")
        return 1


async def cmd_status() -> int:
    """Print terminal summary of daemon and sync profiles."""
    table = Table(title="Rclone AutoSync - Profiles")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold cyan")
    table.add_column("Mode", style="magenta")
    table.add_column("Local Path")
    table.add_column("Remote Target", style="green")
    table.add_column("Watch")
    table.add_column("BW Limit")
    table.add_column("Last Sync")

    profiles = load_profiles()
    for p in profiles:
        table.add_row(
            p.id,
            p.name,
            p.mode.value.upper(),
            p.local_path,
            p.full_remote_target,
            "✓" if p.watch_enabled else "✗",
            p.bwlimit or "Default",
            format_iso_timestamp(p.last_sync_time),
        )

    srv_status = await get_service_status_summary()
    client = DaemonClient()
    sock_alive = await client.is_running()

    console.print(f"[bold]systemd User Service:[/bold] {srv_status}")
    console.print(f"[bold]Daemon IPC Socket:[/bold] {'ONLINE' if sock_alive else 'OFFLINE'}")
    console.print()
    console.print(table)
    return 0


async def cmd_service(action: str) -> int:
    """Manage systemd user service."""
    if action == "install":
        ok, msg = await install_service(start_now=True)
        console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return 0 if ok else 1
    elif action == "uninstall":
        ok, msg = await uninstall_service()
        console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return 0 if ok else 1
    elif action == "start":
        ok, msg = await start_service()
        console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return 0 if ok else 1
    elif action == "stop":
        ok, msg = await stop_service()
        console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return 0 if ok else 1
    elif action == "restart":
        ok, msg = await restart_service()
        console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return 0 if ok else 1
    elif action == "status":
        summary = await get_service_status_summary()
        console.print(f"[bold]Service Status:[/bold] {summary}")
        return 0
    elif action == "logs":
        logs = await get_recent_journal_logs(lines=50)
        console.print(logs)
        return 0
    else:
        console.print(f"[bold red]Unknown service action:[/bold red] {action}")
        return 1


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="rasync",
        description="Rclone AutoSync - Modern TUI and Daemon for Cloud Sync",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # tui subcommand
    subparsers.add_parser("tui", help="Launch interactive TUI dashboard (default)")

    # daemon subcommand
    subparsers.add_parser("daemon", help="Run background daemon service (called by systemd)")

    # sync subcommand
    sync_parser = subparsers.add_parser("sync", help="Trigger sync for a profile")
    sync_parser.add_argument("profile", help="Profile name or ID")
    sync_parser.add_argument("--dry-run", action="store_true", help="Perform dry-run simulation")
    sync_parser.add_argument("--resync", action="store_true", help="Perform first-time or recovery --resync for bisync")

    # status subcommand
    subparsers.add_parser("status", help="Show profiles and daemon status")

    # service subcommand
    srv_parser = subparsers.add_parser("service", help="Manage systemd user service")
    srv_parser.add_argument(
        "action",
        choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"],
        help="Service management action",
    )

    args = parser.parse_args()

    # Default action: run TUI
    if not args.command or args.command == "tui":
        run_tui()
        return

    if args.command == "daemon":
        asyncio.run(run_daemon())
    elif args.command == "sync":
        code = asyncio.run(cmd_sync(args.profile, dry_run=args.dry_run, resync=args.resync))
        sys.exit(code)
    elif args.command == "status":
        code = asyncio.run(cmd_status())
        sys.exit(code)
    elif args.command == "service":
        code = asyncio.run(cmd_service(args.action))
        sys.exit(code)


if __name__ == "__main__":
    main()
