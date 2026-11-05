"""Dual-pane local & Google Drive remote folder explorer."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import (
    Button,
    DirectoryTree,
    Label,
    ListView,
    ListItem,
    Select,
    Static,
)

from ...models import SyncProfile
from ...rclone.client import list_remote_dirs, list_remotes
from .profile_modal import ProfileModal


class RemoteDirItem(ListItem):
    """List item representing a remote folder or parent folder navigation."""

    def __init__(self, dir_name: str, is_up: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.dir_name = dir_name
        self.is_up = is_up

    def compose(self) -> ComposeResult:
        if self.is_up:
            yield Label("📁 .. (Go to parent directory)", classes="stat-label")
        else:
            yield Label(f"📁 {self.dir_name}", classes="stat-value")


class ExplorerTab(Widget):
    """Interactive explorer for browsing local files and Google Drive folders."""

    def __init__(self, **kwargs):
        super().__init__(id="explorer-container", **kwargs)
        self.selected_local_path: str = str(Path.home())
        self.selected_remote: str = ""
        self.current_remote_subpath: str = ""
        self.selected_remote_full_path: str = ""

    def compose(self) -> ComposeResult:
        with Horizontal():
            # Left pane: Local directories
            with Vertical(classes="explorer-pane", id="local-explorer-pane"):
                yield Label("Local Directory Tree", classes="pane-title")
                yield Label(f"Selected: {self.selected_local_path}", id="selected-local-label")
                yield DirectoryTree(str(Path.home()), id="local-directory-tree")

            # Right pane: Remote Google Drive
            with Vertical(classes="explorer-pane", id="remote-explorer-pane"):
                yield Label("Google Drive / Remote Explorer", classes="pane-title")

                with Horizontal(id="remote-controls"):
                    yield Label("Remote: ", classes="stat-label")
                    yield Select(options=[("Loading...", "")], value="", id="select-explorer-remote")

                yield Label("Path: /", id="remote-current-path-label")
                yield ListView(id="remote-dirs-list")

                with Horizontal(id="explorer-actions"):
                    yield Button("Refresh Drive", variant="default", id="btn-refresh-remote")
                    yield Button(
                        "+ Create Profile From Selected",
                        variant="primary",
                        classes="primary",
                        id="btn-create-from-selected",
                    )

    async def on_mount(self) -> None:
        remotes = await list_remotes()
        remote_select = self.query_one("#select-explorer-remote", Select)
        if remotes:
            remote_select.set_options([(r, r) for r in remotes])
            self.selected_remote = remotes[0]
            remote_select.value = remotes[0]
            await self._load_remote_directories()
        else:
            remote_select.set_options([("No remotes configured", "")])

    async def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "select-explorer-remote":
            new_remote = str(event.value)
            if new_remote and new_remote != self.selected_remote:
                self.selected_remote = new_remote
                self.current_remote_subpath = ""
                await self._load_remote_directories()

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self.selected_local_path = str(event.path)
        lbl = self.query_one("#selected-local-label", Label)
        lbl.update(f"Selected: {self.selected_local_path}")

    async def _load_remote_directories(self) -> None:
        """Fetch directories inside current_remote_subpath using rclone."""
        if not self.selected_remote:
            return

        list_view = self.query_one("#remote-dirs-list", ListView)
        list_view.clear()

        path_lbl = self.query_one("#remote-current-path-label", Label)
        display_subpath = f"/{self.current_remote_subpath}" if self.current_remote_subpath else "/"
        path_lbl.update(f"Path: {self.selected_remote}:{display_subpath}")

        # If we are inside a subfolder, add ".." parent directory item
        if self.current_remote_subpath:
            list_view.append(RemoteDirItem("..", is_up=True))

        dirs = await list_remote_dirs(
            remote=self.selected_remote,
            path=self.current_remote_subpath,
        )

        for d in dirs:
            list_view.append(RemoteDirItem(d))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, RemoteDirItem):
            if item.is_up:
                # Go up one level
                parts = [p for p in self.current_remote_subpath.split("/") if p]
                if parts:
                    parts.pop()
                    self.current_remote_subpath = "/".join(parts)
            else:
                # Enter folder
                if self.current_remote_subpath:
                    self.current_remote_subpath = f"{self.current_remote_subpath}/{item.dir_name}"
                else:
                    self.current_remote_subpath = item.dir_name

            await self._load_remote_directories()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh-remote":
            await self._load_remote_directories()
        elif event.button.id == "btn-create-from-selected":
            self._create_profile_with_selected()

    def _create_profile_with_selected(self) -> None:
        folder_name = Path(self.selected_local_path).name or "Sync"
        profile_name = f"{folder_name} Sync"

        prefilled = SyncProfile(
            name=profile_name,
            local_path=self.selected_local_path,
            remote=self.selected_remote,
            remote_path=self.current_remote_subpath,
        )

        def modal_cb(saved: Optional[SyncProfile]) -> None:
            if saved:
                from ...config import upsert_profile
                upsert_profile(saved)
                self.app.notify(f"Profile '{saved.name}' created!", severity="information")

        self.app.push_screen(ProfileModal(profile=prefilled), modal_cb)
