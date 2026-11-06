"""Diagnostic health check for rclone, systemd, and Linux inotify limits."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Dict, List, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import get_config_dir, get_socket_path, load_profiles
from ..daemon.ipc import DaemonClient
from ..daemon.power import get_power_status
from ..daemon.systemd import get_service_status_summary, is_service_active, is_service_installed
from ..rclone.client import get_rclone_version, is_rclone_available, list_remotes
from .wizard import count_subdirectories

console = Console()


def get_sysctl_value(key: str) -> int:
    """Read a sysctl value from /proc/sys/."""
    proc_path = Path("/proc/sys") / key.replace(".", "/")
    try:
        if proc_path.exists():
            return int(proc_path.read_text().strip())
    except Exception:
        pass
    return -1


async def run_doctor_diagnostics() -> int:
    """Execute complete system and configuration diagnostics."""
    console.print(Panel.fit("[bold cyan]🩺 Rclone AutoSync Doctor - System Diagnostics[/bold cyan]", border_style="cyan"))

    results: List[Tuple[str, str, str, str]] = []  # Component, Status (PASS/WARN/FAIL), Value, Recommendation
    has_warnings = False
    has_errors = False

    # 1. Rclone binary check
    if is_rclone_available():
        version = await get_rclone_version() or "Available"
        results.append(("rclone Binary", "[bold green]PASS[/bold green]", version, "OK"))
    else:
        results.append(("rclone Binary", "[bold red]FAIL[/bold red]", "Not found in PATH", "Install rclone: 'sudo pacman -S rclone'"))
        has_errors = True

    # 2. Rclone remotes check
    remotes = await list_remotes()
    if remotes:
        remote_str = ", ".join(remotes)
        results.append(("rclone Remotes", "[bold green]PASS[/bold green]", f"{len(remotes)} remotes ({remote_str})", "OK"))
    else:
        results.append(("rclone Remotes", "[bold yellow]WARN[/bold yellow]", "No remotes found", "Run 'rclone config' to connect Google Drive"))
        has_warnings = True

    # 3. Linux Inotify Kernel Limits & Directory Counts
    max_queued_events = get_sysctl_value("fs.inotify.max_queued_events")
    max_user_watches = get_sysctl_value("fs.inotify.max_user_watches")

    profiles = load_profiles()
    watched_profiles = [p for p in profiles if p.enabled and p.watch_enabled]
    total_watched_dirs = 0

    for p in watched_profiles:
        loc = p.resolved_local_path
        if loc.exists():
            c = count_subdirectories(loc, max_count=35000)
            total_watched_dirs += c

    if max_queued_events > 0:
        if total_watched_dirs > max_queued_events:
            msg = f"{total_watched_dirs} dirs > queue limit ({max_queued_events})"
            fix = "Run: sudo sysctl fs.inotify.max_queued_events=131072"
            results.append(("Inotify Queue Limit", "[bold red]FAIL[/bold red]", msg, fix))
            has_errors = True
        elif total_watched_dirs > (max_queued_events * 0.7):
            msg = f"{total_watched_dirs} dirs close to limit ({max_queued_events})"
            fix = "Consider raising: sudo sysctl fs.inotify.max_queued_events=131072"
            results.append(("Inotify Queue Limit", "[bold yellow]WARN[/bold yellow]", msg, fix))
            has_warnings = True
        else:
            msg = f"Limit: {max_queued_events} (Watching ~{total_watched_dirs} dirs)"
            results.append(("Inotify Queue Limit", "[bold green]PASS[/bold green]", msg, "OK"))
    else:
        results.append(("Inotify Limits", "[dim]INFO[/dim]", "Non-Linux or restricted /proc", "N/A"))

    # 4. Background systemd Service
    installed = await is_service_installed()
    active = await is_service_active()
    summary = await get_service_status_summary()

    if installed and active:
        results.append(("systemd Service", "[bold green]PASS[/bold green]", "active (running)", "OK"))
    elif installed:
        results.append(("systemd Service", "[bold yellow]WARN[/bold yellow]", f"Installed but {summary}", "Run 'rasync service start'"))
        has_warnings = True
    else:
        results.append(("systemd Service", "[dim]INFO[/dim]", "Not installed", "Run 'rasync service install' for background auto-sync"))

    # 5. IPC Daemon Socket
    client = DaemonClient()
    sock_alive = await client.is_running()
    if sock_alive:
        results.append(("IPC Daemon Socket", "[bold green]PASS[/bold green]", "Online & reachable", "OK"))
    else:
        status_str = "Offline" if installed else "Offline (Manual CLI mode)"
        results.append(("IPC Daemon Socket", "[dim]INFO[/dim]", status_str, "Daemon starts when service is active"))

    # 6. Desktop notifications check
    notify_send_installed = shutil.which("notify-send") is not None
    if notify_send_installed:
        results.append(("Desktop Notifier", "[bold green]PASS[/bold green]", "notify-send available", "OK"))
    else:
        results.append(("Desktop Notifier", "[dim]INFO[/dim]", "notify-send not found", "Install libnotify for toast alerts"))

    # 7. Power / Charger status check
    is_ac, has_battery, power_desc = get_power_status()
    if not has_battery:
        results.append(("Power / Battery", "[bold green]PASS[/bold green]", power_desc, "Always AC (Desktop)"))
    elif is_ac:
        results.append(("Power / Battery", "[bold green]PASS[/bold green]", power_desc, "AC Connected (Full sync allowed)"))
    else:
        results.append(("Power / Battery", "[bold yellow]WARN[/bold yellow]", power_desc, "Battery mode: sync_on_ac waiting, pause_on_battery active"))

    # Render results table
    table = Table(box=None, header_style="bold cyan", padding=(0, 2))
    table.add_column("Diagnostic Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Detected Value", style="white")
    table.add_column("Remedy / Notes", style="dim")

    for comp, status, val, notes in results:
        table.add_row(comp, status, val, notes)

    console.print(table)
    console.print()

    if has_errors:
        console.print("[bold red]✖ Issues detected that may impact synchronization.[/bold red]")
        console.print("Please apply the remedies above.\n")
        return 1
    elif has_warnings:
        console.print("[bold yellow]⚠️ System passed with warnings.[/bold yellow]\n")
        return 0
    else:
        console.print("[bold green]✔ All systems healthy! Ready for high-performance syncing.[/bold green]\n")
        return 0
