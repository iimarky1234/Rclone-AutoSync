"""Data models for rclone-autosync."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class SyncMode(str, Enum):
    SYNC = "sync"       # 1-way mirror (remote matches local, remote extras deleted/trashed)
    COPY = "copy"       # 1-way upload (only adds/updates, keeps remote extras)
    BISYNC = "bisync"   # 2-way bidirectional sync


class ProfileStatus(str, Enum):
    IDLE = "idle"
    WATCHING = "watching"
    QUEUED = "queued"
    SYNCING = "syncing"
    ERROR = "error"
    DISABLED = "disabled"


DEFAULT_EXCLUDES = [
    ".git/**",
    "**/.DS_Store",
    "**/node_modules/**",
    "**/__pycache__/**",
    "*.tmp",
    "*~",
    ".pytest_cache/**",
    ".venv/**",
    "**/.cache/**",
    "**/.Trash*/**",
    "**/$RECYCLE.BIN/**",
    "**/System Volume Information/**",
]


class SyncProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    local_path: str
    remote: str
    remote_path: str
    mode: SyncMode = SyncMode.SYNC
    watch_enabled: bool = True
    debounce_seconds: int = 10
    schedule_interval_minutes: Optional[int] = None
    sync_on_ac: bool = False
    pause_on_battery: bool = False
    bwlimit: Optional[str] = None
    use_trash: bool = True
    excludes: List[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    extra_flags: List[str] = Field(default_factory=list)
    enabled: bool = True
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_sync_time: Optional[str] = None
    last_sync_status: Optional[str] = None
    last_sync_message: Optional[str] = None

    @property
    def full_remote_target(self) -> str:
        """Returns Remote:Path normalized."""
        clean_remote = self.remote.rstrip(":")
        clean_path = self.remote_path.strip("/")
        return f"{clean_remote}:{clean_path}"

    @property
    def resolved_local_path(self) -> Path:
        return Path(self.local_path).expanduser().resolve()


class GlobalConfig(BaseModel):
    socket_path: str = "~/.config/rasync/rasync.sock"
    log_file: str = "~/.config/rasync/daemon.log"
    log_level: str = "INFO"
    default_bwlimit: Optional[str] = None
    enable_desktop_notifications: bool = True
    rclone_path: str = "rclone"
    transfers: int = 4
    checkers: int = 8


class ActiveFileTransfer(BaseModel):
    name: str
    size: int = 0
    bytes: int = 0
    percentage: int = 0
    speed: float = 0.0
    eta: Optional[int] = None

    @model_validator(mode="after")
    def compute_percentage(self) -> ActiveFileTransfer:
        if self.size > 0 and self.percentage == 0 and self.bytes > 0:
            self.percentage = int(min(100, (self.bytes / self.size) * 100))
        return self


class TransferProgress(BaseModel):
    profile_id: str
    profile_name: str
    status: str = "syncing"
    dry_run: bool = False
    bytes_transferred: int = 0
    total_bytes: int = 0
    percentage: float = 0.0
    speed: float = 0.0
    eta_seconds: Optional[int] = None
    transfers: int = 0
    total_transfers: int = 0
    deletes: int = 0
    checks: int = 0
    total_checks: int = 0
    elapsed_time: float = 0.0
    errors: int = 0
    active_files: List[ActiveFileTransfer] = Field(default_factory=list)
    current_message: str = ""

    @model_validator(mode="after")
    def compute_percentage(self) -> TransferProgress:
        if self.total_bytes > 0 and self.percentage == 0.0:
            self.percentage = round(min(100.0, (self.bytes_transferred / self.total_bytes) * 100.0), 1)
        return self


class SyncRunResult(BaseModel):
    profile_id: str
    profile_name: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    success: bool
    dry_run: bool = False
    duration_seconds: float = 0.0
    bytes_transferred: int = 0
    files_transferred: int = 0
    files_deleted: int = 0
    files_checked: int = 0
    error_message: Optional[str] = None
    action_log: List[str] = Field(default_factory=list)
