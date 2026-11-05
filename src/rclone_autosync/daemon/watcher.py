"""Watchdog-based recursive directory watcher with async debounce."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..models import SyncProfile

logger = logging.getLogger(__name__)


def should_ignore_path(rel_path: str, excludes: List[str]) -> bool:
    """Check if a relative path matches any exclusion patterns."""
    normalized = rel_path.replace("\\", "/")
    for pattern in excludes:
        p = pattern.strip()
        if not p:
            continue
        # Check standard fnmatch and directory prefix patterns
        if fnmatch.fnmatch(normalized, p) or fnmatch.fnmatch(f"/{normalized}", p):
            return True
        if p.endswith("/**"):
            prefix = p[:-3].rstrip("/")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
        elif p.endswith("/*"):
            prefix = p[:-2].rstrip("/")
            if normalized.startswith(prefix + "/"):
                return True
    return False


class DebouncedEventHandler(FileSystemEventHandler):
    """File event handler that filters excludes and debounces events."""

    def __init__(
        self,
        profile: SyncProfile,
        loop: asyncio.AbstractEventLoop,
        trigger_callback: Callable[[str, str], None],
    ):
        super().__init__()
        self.profile = profile
        self.loop = loop
        self.trigger_callback = trigger_callback
        self.root_path = profile.resolved_local_path
        self._timer_handle: Optional[asyncio.TimerHandle] = None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        # Calculate relative path
        try:
            event_path = Path(event.src_path).resolve()
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
    ):
        self.loop = loop
        self.trigger_callback = trigger_callback
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
