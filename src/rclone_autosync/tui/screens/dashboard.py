"""Dashboard tab displaying sync profiles, controls, and live transfer progress."""

from __future__ import annotations

from typing import Dict, List, Optional
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, DataTable, Label, Static

from ...config import (
    delete_profile,
    load_profiles,
    save_profiles,
    upsert_profile,
)
from ...daemon.ipc import DaemonClient
from ...models import ProfileStatus, SyncMode, SyncProfile, TransferProgress
from ...rclone.executor import RcloneExecutor, has_bisync_listings
from ..formatters import format_iso_timestamp
from ..widgets.status_pill import StatusPill
from ..widgets.transfer_card import TransferCard
from .dryrun_modal import DryRunModal
from .profile_modal import ProfileModal


class DashboardTab(Widget):
    """Main dashboard tab container."""

    BINDINGS = [
        Binding("space", "sync_selected", "Sync Now", priority=True),
        Binding("b", "resync_selected", "Resync (Init)", priority=True),
        Binding("d", "dry_run_selected", "Dry Run", priority=True),
        Binding("w", "toggle_watch_selected", "Toggle Watch", priority=True),
        Binding("n", "new_profile", "New Profile", priority=True),
        Binding("e", "edit_selected", "Edit Profile", priority=True),
        Binding("delete", "delete_selected", "Delete Profile", priority=True),
        Binding("r", "refresh_data", "Refresh", priority=True),
    ]

    def __init__(self, client: DaemonClient, **kwargs):
        super().__init__(id="dashboard-container", **kwargs)
        self.client = client
        self.profiles: List[SyncProfile] = []
        self.statuses: Dict[str, str] = {}
        self.selected_profile_id: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="profiles-header-box"):
                with Horizontal():
                    yield Label("Sync Profiles  ", classes="card-title")
                    yield Label("Daemon: Checking...", id="daemon-status-indicator", classes="stat-label")

                with Horizontal():
                    yield Button("+ New Profile", variant="primary", classes="primary", id="btn-new-profile")
                    yield Button("Sync Now", variant="success", classes="success", id="btn-sync-now")
                    yield Button("Resync", variant="warning", classes="warning", id="btn-resync")
                    yield Button("Dry Run", variant="default", id="btn-dry-run")
                    yield Button("Toggle Watch", variant="default", id="btn-toggle-watch")
                    yield Button("Edit", variant="default", id="btn-edit")
                    yield Button("Delete", variant="error", classes="danger", id="btn-delete")

            yield DataTable(id="profiles-table", cursor_type="row")
            yield Static(
                "✨ [bold #38bdf8]Welcome to Rclone AutoSync![/] No sync profiles configured yet.\n"
                "Click [bold #0284c7][+ New Profile][/] (or press [bold #f8fafc][N][/]) to configure your first folder, "
                "or switch to the [bold #38bdf8]📁 Drive Explorer[/] tab to browse your Google Drive.",
                id="empty-profiles-notice",
            )
            yield TransferCard()

    async def on_mount(self) -> None:
        table = self.query_one("#profiles-table", DataTable)
        table.add_columns(
            "Status",
            "Name",
            "Mode",
            "Local Folder",
            "Remote Destination",
            "Watch",
            "BW Limit",
            "Last Synced",
        )
        await self.refresh_dashboard()

    async def refresh_dashboard(self) -> None:
        """Fetch profiles and statuses, then re-populate the table."""
        self.profiles = load_profiles()
        table = self.query_one("#profiles-table", DataTable)
        daemon_indicator = self.query_one("#daemon-status-indicator", Label)

        is_running = await self.client.is_running()
        if is_running:
            daemon_indicator.update("Daemon: [ONLINE]")
            try:
                state_resp = await self.client.send_command("get_state")
                data = state_resp.get("data", {})
                self.statuses = data.get("statuses", {})
                current_prog = data.get("current_progress")
                if current_prog:
                    card = self.query_one(TransferCard)
                    card.update_progress(TransferProgress.model_validate(current_prog))
            except Exception:
                pass
        else:
            daemon_indicator.update("Daemon: [OFFLINE] (Start via Daemon tab)")
            self.statuses = {}

        # Save current cursor row index
        prev_cursor_row = table.cursor_row

        table.clear()
        for p in self.profiles:
            status_val = self.statuses.get(p.id, "watching" if p.watch_enabled else "idle")
            pill = StatusPill(status=status_val)
            table.add_row(
                pill.render(),
                p.name,
                p.mode.value.upper(),
                p.local_path,
                p.full_remote_target,
                "✓ Enabled" if p.watch_enabled else "✗ Disabled",
                p.bwlimit or "Default",
                format_iso_timestamp(p.last_sync_time),
                key=p.id,
            )

        empty_notice = self.query_one("#empty-profiles-notice", Static)
        if self.profiles:
            empty_notice.display = False
            # Restore selection or select first
            row_idx = min(prev_cursor_row, len(self.profiles) - 1) if prev_cursor_row >= 0 else 0
            table.move_cursor(row=row_idx)
            self.selected_profile_id = self.profiles[row_idx].id
        else:
            empty_notice.display = True
            self.selected_profile_id = None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key and event.row_key.value:
            self.selected_profile_id = str(event.row_key.value)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.selected_profile_id = str(event.row_key.value)

    def get_selected_profile(self) -> Optional[SyncProfile]:
        if not self.selected_profile_id:
            return None
        return next((p for p in self.profiles if p.id == self.selected_profile_id), None)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new-profile":
            await self.action_new_profile()
        elif event.button.id == "btn-sync-now":
            await self.action_sync_selected()
        elif event.button.id == "btn-resync":
            await self.action_resync_selected()
        elif event.button.id == "btn-dry-run":
            await self.action_dry_run_selected()
        elif event.button.id == "btn-toggle-watch":
            await self.action_toggle_watch_selected()
        elif event.button.id == "btn-edit":
            await self.action_edit_selected()
        elif event.button.id == "btn-delete":
            await self.action_delete_selected()

    async def action_new_profile(self) -> None:
        def callback(new_p: Optional[SyncProfile]) -> None:
            if new_p:
                upsert_profile(new_p)
                self.app.notify(f"Profile '{new_p.name}' created", severity="information")
                self.run_worker(self._after_profile_changed())

        self.app.push_screen(ProfileModal(), callback)

    async def action_edit_selected(self) -> None:
        p = self.get_selected_profile()
        if not p:
            self.app.notify("Select a profile to edit", severity="warning")
            return

        def callback(updated_p: Optional[SyncProfile]) -> None:
            if updated_p:
                upsert_profile(updated_p)
                self.app.notify(f"Profile '{updated_p.name}' updated", severity="information")
                self.run_worker(self._after_profile_changed())

        self.app.push_screen(ProfileModal(profile=p), callback)

    async def action_delete_selected(self) -> None:
        p = self.get_selected_profile()
        if not p:
            self.app.notify("Select a profile to delete", severity="warning")
            return

        delete_profile(p.id)
        self.app.notify(f"Profile '{p.name}' deleted", severity="information")
        await self._after_profile_changed()

    async def action_toggle_watch_selected(self) -> None:
        p = self.get_selected_profile()
        if not p:
            self.app.notify("Select a profile to toggle watch", severity="warning")
            return

        p.watch_enabled = not p.watch_enabled
        upsert_profile(p)
        status_msg = "enabled" if p.watch_enabled else "disabled"
        self.app.notify(f"Watcher {status_msg} for '{p.name}'", severity="information")
        await self._after_profile_changed()

    async def action_sync_selected(self) -> None:
        p = self.get_selected_profile()
        if not p:
            self.app.notify("Select a profile to sync", severity="warning")
            return

        need_resync = False
        if p.mode == SyncMode.BISYNC and not has_bisync_listings(p):
            need_resync = True
            self.app.notify("First-time bisync: Initializing baseline with --resync...", severity="warning")

        if await self.client.is_running():
            try:
                resp = await self.client.send_command("sync_now", profile_id=p.id, resync=need_resync)
                self.app.notify(f"Queued sync for '{p.name}'", severity="information")
            except Exception as e:
                self.app.notify(f"Failed to queue sync: {e}", severity="error")
        else:
            # Standalone execution if daemon not running
            self.app.notify(f"Running sync directly for '{p.name}'...", severity="information")
            self.run_worker(self._run_direct_sync(p, dry_run=False, resync=need_resync))

    async def action_resync_selected(self) -> None:
        p = self.get_selected_profile()
        if not p:
            self.app.notify("Select a profile to resync", severity="warning")
            return

        if p.mode != SyncMode.BISYNC:
            self.app.notify("Resync is only applicable to Bidirectional (bisync) profiles", severity="information")
            return

        self.app.notify(f"Running baseline Resync (--resync) for '{p.name}'...", severity="warning")
        if await self.client.is_running():
            try:
                await self.client.send_command("sync_now", profile_id=p.id, resync=True)
                self.app.notify(f"Queued resync for '{p.name}'", severity="information")
            except Exception as e:
                self.app.notify(f"Failed to queue resync: {e}", severity="error")
        else:
            self.run_worker(self._run_direct_sync(p, dry_run=False, resync=True))

    async def action_dry_run_selected(self) -> None:
        p = self.get_selected_profile()
        if not p:
            self.app.notify("Select a profile for dry-run", severity="warning")
            return

        self.app.notify(f"Simulating dry-run for '{p.name}'...", severity="information")
        self.run_worker(self._run_dry_run_analysis(p))

    async def _run_dry_run_analysis(self, profile: SyncProfile) -> None:
        executor = RcloneExecutor(profile=profile)
        result = await executor.run(dry_run=True)

        def modal_callback(execute_now: bool) -> None:
            if execute_now:
                self.run_worker(self._run_direct_sync(profile, dry_run=False))

        self.app.push_screen(DryRunModal(profile=profile, result=result), modal_callback)

    async def _run_direct_sync(
        self,
        profile: SyncProfile,
        dry_run: bool = False,
        resync: bool = False,
    ) -> None:
        card = self.query_one(TransferCard)
        card.update_progress(
            TransferProgress(
                profile_id=profile.id,
                profile_name=profile.name,
                status="syncing",
                dry_run=dry_run,
                current_message="Initializing sync engine...",
            )
        )
        executor = RcloneExecutor(profile=profile)

        def on_prog(prog: TransferProgress) -> None:
            card.update_progress(prog)

        result = await executor.run(dry_run=dry_run, resync=resync, on_progress=on_prog)
        card.update_progress(None)

        if result.success:
            self.app.notify(
                f"Sync '{profile.name}' completed ({result.files_transferred} files)",
                severity="information",
            )
        else:
            self.app.notify(f"Sync error: {result.error_message}", severity="error")

        await self.refresh_dashboard()

    async def _after_profile_changed(self) -> None:
        if await self.client.is_running():
            try:
                await self.client.send_command("reload_profiles")
            except Exception:
                pass
        await self.refresh_dashboard()

    async def action_refresh_data(self) -> None:
        await self.refresh_dashboard()
