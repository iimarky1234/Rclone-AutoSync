"""Watchdog-based recursive directory watcher with async debounce."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import get_config_dir
from ..models import SyncProfile

logger = logging.getLogger(__name__)

# Patch watchdog's inotify engine on Linux to prune high-churn and excluded folders
# This prevents kernel inotify queue overflow (IN_Q_OVERFLOW) and runaway CPU loops
try:
    import os
    from watchdog.observers.inotify import InotifyWatchGroup

    PRUNED_DIR_BYTES = {
        b"zalodata",
        b"rasync",
        b".git",
        b"node_modules",
        b"__pycache__",
        b".cache",
        b"cache",
        b".pytest_cache",
        b".venv",
        b"venv",
        b"$recycle.bin",
        b"system volume information",
    }

    def _patched_add_all_callbacks(self: Any, path: bytes) -> None:
        is_dir = os.path.isdir(path)
        self._add_callback(path)
        if is_dir and self.is_recursive:
            for root, dirnames, _ in os.walk(path, followlinks=self.follow_symlink):
                dirnames[:] = [d for d in dirnames if d.lower() not in PRUNED_DIR_BYTES]
                for dirname in dirnames:
                    full_path = os.path.join(root, dirname)
                    if not self.follow_symlink and os.path.islink(full_path):
                        continue
                    self._add_callback(full_path)

    InotifyWatchGroup._add_all_callbacks = _patched_add_all_callbacks
    logger.debug("Successfully installed inotify directory pruning patch")
except Exception as e:
    logger.debug("Inotify directory pruning patch skipped: %s", e)


def should_ignore_path(rel_path: str, excludes: List[str]) -> bool:
    """Check if a relative path matches any exclusion patterns."""
    normalized = rel_path.replace("\\", "/").strip("/")
    parts = normalized.split("/")

    for pattern in excludes:
        p = pattern.strip()
        if not p:
            continue

        # Match exact folder name in any part of the path (e.g. "ZaloData", "rasync")
        if p in parts:
            return True
        if normalized == p or normalized.startswith(p + "/"):
            return True

        # Check standard fnmatch and directory prefix patterns
        if fnmatch.fnmatch(normalized, p) or fnmatch.fnmatch(f"/{normalized}", p):
            return True
        if p.endswith("/**"):
            prefix = p[:-3].rstrip("/")
            if normalized == prefix or normalized.startswith(prefix + "/") or prefix in parts:
                return True
        elif p.endswith("/*"):
            prefix = p[:-2].rstrip("/")
            if normalized.startswith(prefix + "/") or prefix in parts:
                return True
    return False


class DebouncedEventHandler(FileSystemEventHandler):
    """File event handler that filters excludes and debounces events."""

    def __init__(
        self,
        profile: SyncProfile,
        loop: asyncio.AbstractEventLoop,
        trigger_callback: Callable[[str, str], None],
        is_syncing_callback: Optional[Callable[[str], bool]] = None,
    ):
        super().__init__()
        self.profile = profile
        self.loop = loop
        self.trigger_callback = trigger_callback
        self.is_syncing_callback = is_syncing_callback
        self.root_path = profile.resolved_local_path
        self._timer_handle: Optional[asyncio.TimerHandle] = None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        # Suppress inotify feedback loop when this profile is actively syncing
        if self.is_syncing_callback and self.is_syncing_callback(self.profile.id):
            return

        # Calculate relative path
        try:
            event_path = Path(event.src_path).resolve()
            # Always ignore rasync's own config/log files to prevent infinite feedback loops
            config_dir = get_config_dir().resolve()
            if event_path == config_dir or config_dir in event_path.parents:
                return

            rel_path = str(event_path.relative_to(self.root_path))
        except Exception:
            rel_path = event.src_path

        # Check exclusion rules
        if should_ignore_path(rel_path, self.profile.excludes):
            return

        logger.debug(
            "Change detected in [%s]: %s (%s)",
            self.profile.name,
            rel_path,
            event.event_type,
        )

        # Thread-safe schedule onto the asyncio event loop
        self.loop.call_soon_threadsafe(self._reset_debounce_timer)

    def _reset_debounce_timer(self) -> None:
        if self._timer_handle:
            self._timer_handle.cancel()

        delay = max(2, self.profile.debounce_seconds)
        self._timer_handle = self.loop.call_later(
            delay,
            self._on_debounce_complete,
        )

    def _on_debounce_complete(self) -> None:
        self._timer_handle = None
        logger.info(
            "Debounce quiet period elapsed for profile '%s'. Triggering sync.",
            self.profile.name,
        )
        self.trigger_callback(self.profile.id, "watch")


class WatcherManager:
    """Manages active filesystem observers across all active sync profiles."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        trigger_callback: Callable[[str, str], None],
        is_syncing_callback: Optional[Callable[[str], bool]] = None,
    ):
        self.loop = loop
        self.trigger_callback = trigger_callback
        self.is_syncing_callback = is_syncing_callback
        self._observer: Optional[Observer] = None
        self._watches: Dict[str, Any] = {}  # profile_id -> watchdog watch handle

    def start(self) -> None:
        if self._observer is None:
            self._observer = Observer()
            self._observer.daemon = True
            self._observer.start()
            logger.info("Watchdog observer thread started")

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
            self._watches.clear()
            logger.info("Watchdog observer stopped")

    def update_profiles(self, profiles: List[SyncProfile]) -> None:
        """Update active watches to match the current list of profiles."""
        if not self._observer:
            self.start()

        assert self._observer is not None
        active_ids = {p.id for p in profiles if p.enabled and p.watch_enabled}

        # Remove deleted / disabled watches
        for p_id in list(self._watches.keys()):
            if p_id not in active_ids:
                try:
                    self._observer.unschedule(self._watches[p_id])
                except Exception:
                    pass
                del self._watches[p_id]
                logger.info("Unscheduled watch for profile ID: %s", p_id)

        # Add or refresh watches
        for profile in profiles:
            if not (profile.enabled and profile.watch_enabled):
                continue

            local_path = profile.resolved_local_path
            if not local_path.exists():
                logger.warning(
                    "Local path %s does not exist for profile %s. Skipping watch.",
                    local_path,
                    profile.name,
                )
                continue

            if profile.id not in self._watches:
                try:
                    handler = DebouncedEventHandler(
                        profile=profile,
                        loop=self.loop,
                        trigger_callback=self.trigger_callback,
                        is_syncing_callback=self.is_syncing_callback,
                    )
                    watch = self._observer.schedule(
                        handler,
                        str(local_path),
                        recursive=True,
                    )
                    self._watches[profile.id] = watch
                    logger.info(
                        "Scheduled recursive watch on %s for profile '%s'",
                        local_path,
                        profile.name,
                    )
                except Exception as e:
                    logger.error("Failed to watch %s: %s", local_path, e)
