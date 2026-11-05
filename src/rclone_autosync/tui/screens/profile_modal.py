"""Modal dialog for creating and editing sync profiles."""

from __future__ import annotations

from typing import List, Optional
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select

from ...models import DEFAULT_EXCLUDES, SyncMode, SyncProfile
from ...rclone.client import list_remotes


class ProfileModal(ModalScreen[Optional[SyncProfile]]):
    """Modal dialog for adding or editing a sync profile."""

    def __init__(self, profile: Optional[SyncProfile] = None, **kwargs):
        super().__init__(**kwargs)
        self.profile = profile
        self.is_edit = profile is not None
        self._remotes: List[str] = []

    def compose(self) -> ComposeResult:
        title = "Edit Sync Profile" if self.is_edit else "Create New Sync Profile"

        with Vertical(id="modal-dialog"):
            yield Label(title, id="modal-title")

            with VerticalScroll():
                yield Label("Profile Name:", classes="field-label")
                yield Input(
                    value=self.profile.name if self.profile else "",
                    placeholder="e.g. My Documents Backup",
                    id="input-name",
                )

                yield Label("Local Folder Path:", classes="field-label")
                yield Input(
                    value=self.profile.local_path if self.profile else "",
                    placeholder="/home/user/Documents",
                    id="input-local-path",
                )

                yield Label("Remote Target:", classes="field-label")
                yield Select(
                    options=[("Loading remotes...", "")],
                    value="",
                    id="select-remote",
                )

                yield Label("Remote Subfolder (optional):", classes="field-label")
                yield Input(
                    value=self.profile.remote_path if self.profile else "",
                    placeholder="e.g. Backups/Documents",
                    id="input-remote-path",
                )

                yield Label("Sync Mode:", classes="field-label")
                mode_options = [
                    ("One-Way Mirror (sync: match remote to local, delete extras)", SyncMode.SYNC.value),
                    ("One-Way Upload (copy: upload only, keep remote extras)", SyncMode.COPY.value),
                    ("Two-Way Bidirectional (bisync: sync changes both ways)", SyncMode.BISYNC.value),
                ]
                current_mode = self.profile.mode.value if self.profile else SyncMode.SYNC.value
                yield Select(
                    options=mode_options,
                    value=current_mode,
                    id="select-mode",
                )

                yield Label("Real-time File Watcher:", classes="field-label")
                yield Checkbox(
                    "Enable real-time change auto-sync (debounced)",
                    value=self.profile.watch_enabled if self.profile else True,
                    id="check-watch",
                )

                yield Label("Debounce Quiet Period (seconds):", classes="field-label")
                yield Input(
                    value=str(self.profile.debounce_seconds if self.profile else 10),
                    placeholder="10",
                    id="input-debounce",
                )

                yield Label("Bandwidth Limit (optional, e.g. 5M, 10M, 0 for unlimited):", classes="field-label")
                yield Input(
                    value=self.profile.bwlimit or "" if self.profile else "",
                    placeholder="e.g. 10M or leave empty for default",
                    id="input-bwlimit",
                )

                yield Label("Exclude Patterns (comma separated):", classes="field-label")
                default_ex_str = ", ".join(self.profile.excludes if self.profile else DEFAULT_EXCLUDES)
                yield Input(
                    value=default_ex_str,
                    placeholder=".git/**, node_modules/**, *.tmp",
                    id="input-excludes",
                )

            with Horizontal(id="modal-buttons"):
                yield Button("Cancel", variant="default", id="btn-cancel")
                yield Button("Save Profile", variant="primary", classes="primary", id="btn-save")

    async def on_mount(self) -> None:
        remotes = await list_remotes()
        self._remotes = remotes
        select = self.query_one("#select-remote", Select)

        if remotes:
            options = [(r, r) for r in remotes]
            select.set_options(options)
            if self.profile and self.profile.remote in remotes:
                select.value = self.profile.remote
            else:
                select.value = remotes[0]
        else:
            select.set_options([("No rclone remotes found", "")])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-save":
            self._save()

    def _save(self) -> None:
        name = self.query_one("#input-name", Input).value.strip()
        local_path = self.query_one("#input-local-path", Input).value.strip()
        remote = str(self.query_one("#select-remote", Select).value)
        remote_path = self.query_one("#input-remote-path", Input).value.strip()
        mode_val = str(self.query_one("#select-mode", Select).value)
        watch_enabled = self.query_one("#check-watch", Checkbox).value
        debounce_raw = self.query_one("#input-debounce", Input).value.strip()
        bwlimit_raw = self.query_one("#input-bwlimit", Input).value.strip()
        excludes_raw = self.query_one("#input-excludes", Input).value.strip()

        if not name:
            self.notify("Profile name is required", severity="error")
            return
        if not local_path:
            self.notify("Local folder path is required", severity="error")
            return
        if not remote:
            self.notify("Please select a remote", severity="error")
            return

        try:
            debounce_secs = int(debounce_raw) if debounce_raw else 10
        except ValueError:
            debounce_secs = 10

        excludes = [x.strip() for x in excludes_raw.split(",") if x.strip()]

        if self.is_edit and self.profile:
            p = self.profile.model_copy()
            p.name = name
            p.local_path = local_path
            p.remote = remote
            p.remote_path = remote_path
            p.mode = SyncMode(mode_val)
            p.watch_enabled = watch_enabled
            p.debounce_seconds = debounce_secs
            p.bwlimit = bwlimit_raw or None
            p.excludes = excludes
        else:
            p = SyncProfile(
                name=name,
                local_path=local_path,
                remote=remote,
                remote_path=remote_path,
                mode=SyncMode(mode_val),
                watch_enabled=watch_enabled,
                debounce_seconds=debounce_secs,
                bwlimit=bwlimit_raw or None,
                excludes=excludes,
            )

        self.dismiss(p)
