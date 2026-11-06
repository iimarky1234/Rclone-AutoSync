"""Interactive CLI configuration wizards for adding and editing profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
import questionary
from rich.console import Console
from rich.panel import Panel

from ..config import upsert_profile
from ..models import DEFAULT_EXCLUDES, SyncMode, SyncProfile
from ..rclone.client import list_remote_dirs, list_remotes

console = Console()


def count_subdirectories(path: Path, max_count: int = 15000) -> int:
    """Quickly estimate directory count up to a threshold."""
    count = 0
    try:
        for _, dirnames, _ in os.walk(path):
            count += len(dirnames)
            if count >= max_count:
                return count
    except Exception:
        pass
    return count


async def interactive_add_profile() -> Optional[SyncProfile]:
    """Run interactive wizard to create a new sync profile."""
    console.print(Panel.fit("[bold cyan]✨ New Sync Profile Wizard[/bold cyan]", border_style="cyan"))

    # 1. Profile Name
    name = await questionary.text(
        "Profile Name:",
        validate=lambda text: True if text.strip() else "Profile name cannot be empty",
    ).ask_async()
    if not name:
        return None

    # 2. Local Folder
    local_path_raw = await questionary.path(
        "Local Folder Path:",
        default=str(Path.home()),
        only_directories=True,
    ).ask_async()
    if not local_path_raw:
        return None

    local_path = Path(local_path_raw).expanduser().resolve()
    if not local_path.exists():
        console.print(f"[bold red]Error:[/bold red] Local path '{local_path}' does not exist.")
        return None

    # Check directory scale
    dir_count = count_subdirectories(local_path, max_count=15000)
    if dir_count >= 10000:
        console.print(
            f"[bold yellow]⚠️ Directory Notice:[/bold yellow] Found {dir_count}+ subdirectories under this path.\n"
            f"[dim]Real-time file watching on massive trees can stress Linux inotify queue limits.\n"
            f"Scheduled sync or manual sync is recommended for large partitions.[/dim]\n"
        )

    # 3. Remote Selection
    remotes = await list_remotes()
    if not remotes:
        console.print("[bold red]No rclone remotes found![/bold red] Please run 'rclone config' first.")
        return None

    remote = await questionary.select(
        "Select Remote Cloud Target:",
        choices=remotes,
    ).ask_async()
    if not remote:
        return None

    # 4. Remote Subfolder
    remote_path = await questionary.text(
        f"Remote Destination Folder on {remote}: (leave empty for root):",
        default="",
    ).ask_async()
    if remote_path is None:
        return None

    # 5. Sync Mode
    mode_choices = [
        questionary.Choice("One-Way Mirror (sync: match remote to local, move extras to Trash)", value=SyncMode.SYNC),
        questionary.Choice("One-Way Upload (copy: upload only, keep remote files intact)", value=SyncMode.COPY),
        questionary.Choice("Two-Way Bidirectional (bisync: sync edits between local and cloud)", value=SyncMode.BISYNC),
    ]
    mode = await questionary.select(
        "Synchronization Mode:",
        choices=mode_choices,
    ).ask_async()
    if not mode:
        return None

    # 6. Trigger strategy
    trigger_choices = [
        questionary.Choice("Real-Time Watch (auto-sync when files change via inotify)", value="watch"),
        questionary.Choice("Scheduled Interval (e.g. sync every 30m or 60m)", value="schedule"),
        questionary.Choice("Both (Real-time watch + scheduled interval check)", value="both"),
        questionary.Choice("Manual Only (only run when triggered with 'rasync sync')", value="manual"),
    ]
    trigger_type = await questionary.select(
        "Automation Trigger:",
        choices=trigger_choices,
        default="schedule" if dir_count >= 10000 else "watch",
    ).ask_async()
    if not trigger_type:
        return None

    watch_enabled = trigger_type in ("watch", "both")
    schedule_interval: Optional[int] = None
    debounce_seconds = 10

    if watch_enabled:
        debounce_raw = await questionary.text(
            "Debounce quiet period (seconds to wait after changes before syncing):",
            default="10",
        ).ask_async()
        try:
            debounce_seconds = max(2, int(debounce_raw))
        except Exception:
            debounce_seconds = 10

    if trigger_type in ("schedule", "both"):
        interval_raw = await questionary.text(
            "Schedule interval in minutes (e.g. 30, 60):",
            default="60",
        ).ask_async()
        try:
            schedule_interval = max(1, int(interval_raw))
        except Exception:
            schedule_interval = 60

    # 7. Bandwidth limit
    bwlimit_raw = await questionary.text(
        "Bandwidth Limit (optional, e.g. '10M', '5M', or empty for unlimited):",
        default="",
    ).ask_async()
    bwlimit = bwlimit_raw.strip() if bwlimit_raw and bwlimit_raw.strip() != "0" else None

    profile = SyncProfile(
        name=name.strip(),
        local_path=str(local_path),
        remote=remote,
        remote_path=remote_path.strip("/"),
        mode=mode,
        watch_enabled=watch_enabled,
        debounce_seconds=debounce_seconds,
        schedule_interval_minutes=schedule_interval,
        bwlimit=bwlimit,
        excludes=list(DEFAULT_EXCLUDES),
    )

    upsert_profile(profile)
    console.print(f"\n[bold green]✔ Profile '{profile.name}' created successfully![/bold green]")
    console.print(f"[dim]Run 'rasync sync \"{profile.name}\" --dry-run' to test it.[/dim]\n")
    return profile


async def interactive_edit_profile(profile: SyncProfile) -> Optional[SyncProfile]:
    """Run interactive wizard to update an existing profile."""
    console.print(Panel.fit(f"[bold cyan]Edit Profile: {profile.name}[/bold cyan]", border_style="cyan"))

    name = await questionary.text("Profile Name:", default=profile.name).ask_async()
    if not name:
        return None

    local_path_raw = await questionary.path(
        "Local Folder Path:",
        default=profile.local_path,
        only_directories=True,
    ).ask_async()
    if not local_path_raw:
        return None

    remote_path = await questionary.text(
        f"Remote Destination Folder on {profile.remote}:",
        default=profile.remote_path,
    ).ask_async()
    if remote_path is None:
        return None

    mode_choices = [
        questionary.Choice("One-Way Mirror (sync: match remote to local, move extras to Trash)", value=SyncMode.SYNC),
        questionary.Choice("One-Way Upload (copy: upload only, keep remote files intact)", value=SyncMode.COPY),
        questionary.Choice("Two-Way Bidirectional (bisync: sync edits between local and cloud)", value=SyncMode.BISYNC),
    ]
    mode = await questionary.select(
        "Synchronization Mode:",
        choices=mode_choices,
        default=profile.mode,
    ).ask_async()
    if not mode:
        return None

    watch_enabled = await questionary.confirm(
        "Enable Real-Time File Watcher?",
        default=profile.watch_enabled,
    ).ask_async()

    schedule_interval_raw = await questionary.text(
        "Schedule interval in minutes (leave empty for none):",
        default=str(profile.schedule_interval_minutes or ""),
    ).ask_async()
    schedule_interval = int(schedule_interval_raw) if schedule_interval_raw.strip().isdigit() else None

    bwlimit_raw = await questionary.text(
        "Bandwidth Limit (e.g. '10M' or empty for unlimited):",
        default=profile.bwlimit or "",
    ).ask_async()

    updated = profile.model_copy()
    updated.name = name.strip()
    updated.local_path = str(Path(local_path_raw).expanduser().resolve())
    updated.remote_path = remote_path.strip("/")
    updated.mode = mode
    updated.watch_enabled = bool(watch_enabled)
    updated.schedule_interval_minutes = schedule_interval
    updated.bwlimit = bwlimit_raw.strip() or None

    upsert_profile(updated)
    console.print(f"\n[bold green]✔ Profile '{updated.name}' updated successfully![/bold green]\n")
    return updated
