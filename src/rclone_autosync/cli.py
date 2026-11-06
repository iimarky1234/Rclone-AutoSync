"""Comprehensive Command-Line Interface for rasync and rclone-autosync."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import (
    delete_profile,
    get_log_path,
    get_profile_by_id,
    get_profile_by_name,
    load_history,
    load_profiles,
)
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
from .models import SyncMode, SyncProfile
from .rclone.executor import RcloneExecutor, has_bisync_listings
from .ui.doctor import run_doctor_diagnostics
from .ui.formatters import format_bytes, format_iso_timestamp
from .ui.progress import SyncProgressDisplay
from .ui.watcher_view import run_foreground_watcher
from .ui.wizard import interactive_add_profile, interactive_edit_profile

console = Console()
VERSION = "0.2.0"


def print_banner() -> None:
    """Print welcome banner with quick command overview."""
    banner_text = (
        f"[bold cyan]Rclone AutoSync (rasync)[/bold cyan] [dim]v{VERSION}[/dim]\n"
        f"[dim]Modern, automated CLI synchronizer for Google Drive and cloud remotes[/dim]\n\n"
        f"[bold]Quick Commands:[/bold]\n"
        f"  [cyan]rasync add[/cyan]           Interactive wizard to create a sync profile\n"
        f"  [cyan]rasync list[/cyan]          Display all configured sync profiles\n"
        f"  [cyan]rasync sync <name>[/cyan]   Run sync with live multi-bar progress\n"
        f"  [cyan]rasync watch <name>[/cyan]  Run real-time file watcher in foreground\n"
        f"  [cyan]rasync doctor[/cyan]        Run system and inotify limit health checks\n"
        f"  [cyan]rasync service ...[/cyan]   Manage background systemd auto-sync daemon"
    )
    console.print(Panel(banner_text, border_style="cyan", padding=(1, 2)))


async def cmd_list() -> int:
    """Display rich status table of all profiles and daemon status."""
    profiles = load_profiles()
    srv_status = await get_service_status_summary()
    client = DaemonClient()
    sock_alive = await client.is_running()

    status_color = "green" if "running" in srv_status else "dim"
    sock_color = "green" if sock_alive else "dim"

    console.print(
        f"[bold]systemd User Service:[/bold] [{status_color}]{srv_status.upper()}[/{status_color}]  |  "
        f"[bold]Daemon IPC:[/bold] [{sock_color}]{'ONLINE' if sock_alive else 'OFFLINE'}[/{sock_color}]"
    )

    if not profiles:
        console.print(
            "\n[dim]No sync profiles configured yet.[/dim]\n"
            "Run [bold cyan]rasync add[/bold cyan] to create your first sync profile.\n"
        )
        return 0

    table = Table(
        box=None,
        show_header=True,
        header_style="bold cyan",
        padding=(0, 2),
    )
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold white")
    table.add_column("Mode", style="magenta")
    table.add_column("Local Path", style="dim")
    table.add_column("Remote Target", style="green")
    table.add_column("Triggers", style="yellow")
    table.add_column("BW Limit", style="dim")
    table.add_column("Last Sync", style="white")
    table.add_column("Status", justify="center")

    for p in profiles:
        # Build trigger label
        triggers = []
        if p.watch_enabled:
            triggers.append(f"Watch ({p.debounce_seconds}s)")
        if p.schedule_interval_minutes:
            triggers.append(f"Every {p.schedule_interval_minutes}m")
        if not triggers:
            triggers.append("Manual")
        trigger_str = ", ".join(triggers)

        # Status badge
        if p.last_sync_status == "success":
            status_badge = "[bold green]✔ OK[/bold green]"
        elif p.last_sync_status == "error":
            status_badge = "[bold red]✖ ERR[/bold red]"
        else:
            status_badge = "[dim]NEVER[/dim]"

        table.add_row(
            p.id,
            p.name,
            p.mode.value.upper(),
            p.local_path,
            p.full_remote_target,
            trigger_str,
            p.bwlimit or "Default",
            format_iso_timestamp(p.last_sync_time),
            status_badge,
        )

    console.print()
    console.print(table)
    console.print()
    return 0


async def cmd_sync_single(
    profile: SyncProfile,
    dry_run: bool = False,
    resync: bool = False,
) -> bool:
    """Execute sync for one profile with live Rich visual progress."""
    # Check bisync baseline requirement
    if profile.mode == SyncMode.BISYNC and not resync:
        if not has_bisync_listings(profile):
            console.print(
                f"[yellow]Notice:[/yellow] Profile '{profile.name}' is bidirectional (bisync) and has no baseline listing.\n"
                f"[dim]Automatically enabling '--resync' for initial run.[/dim]\n"
            )
            resync = True

    with SyncProgressDisplay(profile, dry_run=dry_run, console=console) as display:
        executor = RcloneExecutor(profile=profile)
        result = await executor.run(
            dry_run=dry_run,
            resync=resync,
            on_progress=display.update,
        )
        display.print_completion(result)
        return result.success


async def cmd_sync(
    profile_query: Optional[str],
    dry_run: bool = False,
    resync: bool = False,
    sync_all: bool = False,
) -> int:
    """Handle sync subcommand."""
    if sync_all:
        profiles = [p for p in load_profiles() if p.enabled]
        if not profiles:
            console.print("[dim]No enabled profiles found to sync.[/dim]")
            return 0
        console.print(f"[bold cyan]Syncing {len(profiles)} profiles sequentially...[/bold cyan]\n")
        all_ok = True
        for p in profiles:
            ok = await cmd_sync_single(p, dry_run=dry_run, resync=resync)
            if not ok:
                all_ok = False
        return 0 if all_ok else 1

    if not profile_query:
        console.print("[bold red]Error:[/bold red] Please specify a profile name/ID or use '--all'.")
        console.print("[dim]Run 'rasync list' to view available profiles.[/dim]")
        return 1

    profile = get_profile_by_id(profile_query) or get_profile_by_name(profile_query)
    if not profile:
        console.print(f"[bold red]Error:[/bold red] Profile '{profile_query}' not found.")
        console.print("[dim]Run 'rasync list' to view available profiles.[/dim]")
        return 1

    ok = await cmd_sync_single(profile, dry_run=dry_run, resync=resync)
    return 0 if ok else 1


async def cmd_watch(profile_query: str) -> int:
    """Handle watch subcommand."""
    profile = get_profile_by_id(profile_query) or get_profile_by_name(profile_query)
    if not profile:
        console.print(f"[bold red]Error:[/bold red] Profile '{profile_query}' not found.")
        return 1

    await run_foreground_watcher(profile)
    return 0


async def cmd_remove(profile_query: str, yes: bool = False) -> int:
    """Delete a profile."""
    profile = get_profile_by_id(profile_query) or get_profile_by_name(profile_query)
    if not profile:
        console.print(f"[bold red]Error:[/bold red] Profile '{profile_query}' not found.")
        return 1

    if not yes:
        import questionary
        confirm = await questionary.confirm(
            f"Are you sure you want to delete profile '{profile.name}'?",
            default=False,
        ).ask_async()
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            return 0

    delete_profile(profile.id)
    console.print(f"[bold green]✔ Profile '{profile.name}' deleted.[/bold green]")

    # Notify daemon if running
    client = DaemonClient()
    if await client.is_running():
        try:
            await client.send_command("reload_profiles")
        except Exception:
            pass

    return 0


async def cmd_edit(profile_query: str) -> int:
    """Edit an existing profile."""
    profile = get_profile_by_id(profile_query) or get_profile_by_name(profile_query)
    if not profile:
        console.print(f"[bold red]Error:[/bold red] Profile '{profile_query}' not found.")
        return 1

    updated = await interactive_edit_profile(profile)
    if updated:
        client = DaemonClient()
        if await client.is_running():
            try:
                await client.send_command("reload_profiles")
            except Exception:
                pass
    return 0


async def cmd_logs(lines: int = 50, follow: bool = False) -> int:
    """View daemon logs or stream them in real time."""
    if follow:
        cmd = ["journalctl", "--user-unit", "rasync.service", "-f", "-n", str(lines)]
        try:
            subprocess.run(cmd)
            return 0
        except KeyboardInterrupt:
            return 0
    else:
        logs = await get_recent_journal_logs(lines=lines)
        if logs.strip():
            console.print(logs)
        else:
            log_file = get_log_path()
            if log_file.exists():
                console.print(log_file.read_text(encoding="utf-8")[-2000:])
            else:
                console.print("[dim]No service logs found yet.[/dim]")
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
        return await cmd_logs(lines=50, follow=False)
    else:
        console.print(f"[bold red]Unknown service action:[/bold red] {action}")
        return 1


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="rasync",
        description="Rclone AutoSync (rasync) - Automated High-Performance Cloud Synchronizer",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # add / new
    subparsers.add_parser("add", aliases=["new"], help="Create a new sync profile interactively")

    # list / status
    subparsers.add_parser("list", aliases=["status", "ls"], help="List all configured sync profiles")

    # sync
    sync_parser = subparsers.add_parser("sync", help="Synchronize a profile with live visual progress")
    sync_parser.add_argument("profile", nargs="?", help="Profile name or ID")
    sync_parser.add_argument("--all", action="store_true", help="Sync all enabled profiles")
    sync_parser.add_argument("--dry-run", action="store_true", help="Simulate changes without modifying files")
    sync_parser.add_argument("--resync", action="store_true", help="Force baseline resync for bidirectional mode")

    # watch
    watch_parser = subparsers.add_parser("watch", help="Run interactive real-time file watcher in foreground")
    watch_parser.add_argument("profile", help="Profile name or ID to watch")

    # edit
    edit_parser = subparsers.add_parser("edit", help="Edit an existing profile")
    edit_parser.add_argument("profile", help="Profile name or ID to edit")

    # remove / rm
    rm_parser = subparsers.add_parser("remove", aliases=["rm", "delete"], help="Delete a sync profile")
    rm_parser.add_argument("profile", help="Profile name or ID to delete")
    rm_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # doctor
    subparsers.add_parser("doctor", help="Run system, rclone, and Linux inotify health checks")

    # logs
    logs_parser = subparsers.add_parser("logs", help="View daemon and sync logs")
    logs_parser.add_argument("-n", "--lines", type=int, default=50, help="Number of log lines to show")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Follow live journal logs")

    # service
    srv_parser = subparsers.add_parser("service", help="Manage background systemd user service")
    srv_parser.add_argument(
        "action",
        choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"],
        help="Service action",
    )

    # daemon
    subparsers.add_parser("daemon", help="Run background daemon service (executed by systemd)")

    # version
    subparsers.add_parser("version", help="Show version information")

    args = parser.parse_args()

    if not args.command:
        print_banner()
        asyncio.run(cmd_list())
        return

    if args.command in ("add", "new"):
        asyncio.run(interactive_add_profile())
    elif args.command in ("list", "status", "ls"):
        asyncio.run(cmd_list())
    elif args.command == "sync":
        code = asyncio.run(
            cmd_sync(
                profile_query=args.profile,
                dry_run=args.dry_run,
                resync=args.resync,
                sync_all=args.all,
            )
        )
        sys.exit(code)
    elif args.command == "watch":
        code = asyncio.run(cmd_watch(args.profile))
        sys.exit(code)
    elif args.command == "edit":
        code = asyncio.run(cmd_edit(args.profile))
        sys.exit(code)
    elif args.command in ("remove", "rm", "delete"):
        code = asyncio.run(cmd_remove(args.profile, yes=args.yes))
        sys.exit(code)
    elif args.command == "doctor":
        code = asyncio.run(run_doctor_diagnostics())
        sys.exit(code)
    elif args.command == "logs":
        code = asyncio.run(cmd_logs(lines=args.lines, follow=args.follow))
        sys.exit(code)
    elif args.command == "service":
        code = asyncio.run(cmd_service(args.action))
        sys.exit(code)
    elif args.command == "daemon":
        asyncio.run(run_daemon())
    elif args.command == "version":
        console.print(f"[bold cyan]rasync[/bold cyan] version [bold white]{VERSION}[/bold white]")


if __name__ == "__main__":
    main()
