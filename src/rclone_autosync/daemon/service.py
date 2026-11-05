"""Core daemon service coordinator."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from ..config import (
    append_history,
    get_log_path,
    get_profile_by_id,
    load_global_config,
    load_history,
    load_profiles,
    save_profiles,
    upsert_profile,
)
from ..models import (
    GlobalConfig,
    ProfileStatus,
    SyncProfile,
    SyncRunResult,
    TransferProgress,
)
from ..rclone.executor import RcloneExecutor
from .ipc import IPCServer
from .notify import send_notification
from .scheduler import IntervalScheduler
from .watcher import WatcherManager

logger = logging.getLogger(__name__)


class DaemonService:
    """Headless background service managing watchers, schedules, and rclone execution."""

    def __init__(self):
        self.config: GlobalConfig = load_global_config()
        self.profiles: List[SyncProfile] = []
        self.profile_statuses: Dict[str, ProfileStatus] = {}
        self.start_time: float = time.time()

        self.ipc_server: Optional[IPCServer] = None
        self.watcher_manager: Optional[WatcherManager] = None
        self.scheduler: Optional[IntervalScheduler] = None

        self._sync_queue: asyncio.Queue[tuple[str, bool, bool, str]] = asyncio.Queue()  # (profile_id, dry_run, resync, source)
        self._queued_profile_ids: Set[str] = set()
        self._current_executor: Optional[RcloneExecutor] = None
        self._current_progress: Optional[TransferProgress] = None
        self._current_profile_id: Optional[str] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start all daemon components."""
        self._running = True
        self.start_time = time.time()
        loop = asyncio.get_running_loop()

        self._setup_logging()
        logger.info("Initializing Rclone AutoSync Daemon...")

        self.profiles = load_profiles()
        self._init_statuses()

        # 1. Start IPC Server
        self.ipc_server = IPCServer(handler_callback=self.handle_ipc_command)
        await self.ipc_server.start()

        # 2. Start File Watcher
        self.watcher_manager = WatcherManager(
            loop=loop,
            trigger_callback=self.trigger_sync,
        )
        self.watcher_manager.update_profiles(self.profiles)

        # 3. Start Scheduler
        self.scheduler = IntervalScheduler(trigger_callback=self.trigger_sync)
        self.scheduler.set_profiles(self.profiles)
        self.scheduler.start()

        # 4. Start Queue Worker
        self._worker_task = asyncio.create_task(self._process_queue())

        logger.info("Daemon service successfully started with %d profiles", len(self.profiles))

    def _init_statuses(self) -> None:
        for p in self.profiles:
            if not p.enabled:
                self.profile_statuses[p.id] = ProfileStatus.DISABLED
            elif p.watch_enabled:
                self.profile_statuses[p.id] = ProfileStatus.WATCHING
            else:
                self.profile_statuses[p.id] = ProfileStatus.IDLE

    def _setup_logging(self) -> None:
        log_file = get_log_path()
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.config.log_level.upper(), logging.INFO))

        # File handler
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s")
        )
        root_logger.addHandler(file_handler)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s]: %(message)s")
        )
        root_logger.addHandler(console_handler)

    def trigger_sync(
        self,
        profile_id: str,
        source: str = "manual",
        dry_run: bool = False,
        resync: bool = False,
    ) -> bool:
        """Enqueue a sync run for a profile."""
        profile = next((p for p in self.profiles if p.id == profile_id), None)
        if not profile or not profile.enabled:
            return False

        if profile_id in self._queued_profile_ids or self._current_profile_id == profile_id:
            logger.info("Profile '%s' is already syncing or in queue", profile.name)
            return False

        self._queued_profile_ids.add(profile_id)
        self.profile_statuses[profile_id] = ProfileStatus.QUEUED
        self._sync_queue.put_nowait((profile_id, dry_run, resync, source))

        if self.ipc_server:
            asyncio.create_task(
                self.ipc_server.broadcast_event(
                    "status_change",
                    {"profile_id": profile_id, "status": ProfileStatus.QUEUED.value},
                )
            )
        return True

    async def _process_queue(self) -> None:
        """Worker loop executing queued sync jobs sequentially."""
        while self._running:
            try:
                profile_id, dry_run, resync, source = await self._sync_queue.get()
                self._queued_profile_ids.discard(profile_id)

                profile = next((p for p in self.profiles if p.id == profile_id), None)
                if not profile:
                    self._sync_queue.task_done()
                    continue

                self._current_profile_id = profile_id
                self.profile_statuses[profile_id] = ProfileStatus.SYNCING

                init_prog = TransferProgress(
                    profile_id=profile.id,
                    profile_name=profile.name,
                    status="syncing",
                    dry_run=dry_run,
                    current_message="Initializing sync engine...",
                )
                self._current_progress = init_prog

                if self.ipc_server:
                    await self.ipc_server.broadcast_event(
                        "status_change",
                        {"profile_id": profile_id, "status": ProfileStatus.SYNCING.value},
                    )
                    await self.ipc_server.broadcast_event(
                        "progress",
                        init_prog.model_dump(),
                    )

                logger.info(
                    "Starting sync for '%s' (mode: %s, source: %s, dry_run: %s, resync: %s)",
                    profile.name,
                    profile.mode.value,
                    source,
                    dry_run,
                    resync,
                )

                executor = RcloneExecutor(profile=profile, config=self.config)
                self._current_executor = executor

                def on_progress(prog: TransferProgress) -> None:
                    self._current_progress = prog
                    if self.ipc_server:
                        asyncio.create_task(
                            self.ipc_server.broadcast_event("progress", prog.model_dump())
                        )

                def on_log(level: str, msg: str) -> None:
                    if self.ipc_server:
                        asyncio.create_task(
                            self.ipc_server.broadcast_event("log", {"level": level, "message": msg})
                        )

                try:
                    result = await executor.run(
                        dry_run=dry_run,
                        resync=resync,
                        on_progress=on_progress,
                        on_log=on_log,
                    )
                except Exception as e:
                    logger.error("Sync run exception for '%s': %s", profile.name, e)
                    result = SyncRunResult(
                        profile_id=profile.id,
                        profile_name=profile.name,
                        success=False,
                        dry_run=dry_run,
                        error_message=str(e),
                    )

                # Persist results
                append_history(result)
                profile.last_sync_time = datetime.now(timezone.utc).isoformat()
                profile.last_sync_status = "success" if result.success else "error"
                profile.last_sync_message = (
                    "Completed successfully" if result.success else result.error_message
                )
                upsert_profile(profile)

                # Update status
                final_status = (
                    ProfileStatus.WATCHING if profile.watch_enabled else ProfileStatus.IDLE
                )
                if not result.success:
                    final_status = ProfileStatus.ERROR

                self.profile_statuses[profile_id] = final_status
                self._current_progress = None
                self._current_profile_id = None
                self._current_executor = None

                if self.ipc_server:
                    await self.ipc_server.broadcast_event(
                        "status_change",
                        {"profile_id": profile_id, "status": final_status.value},
                    )
                    await self.ipc_server.broadcast_event("sync_completed", result.model_dump())

                # Desktop Notification
                if not result.success:
                    await send_notification(
                        title=f"Sync Error: {profile.name}",
                        message=result.error_message or "An error occurred during sync",
                        urgency="critical",
                        icon="dialog-error",
                    )
                elif result.files_transferred > 0:
                    await send_notification(
                        title=f"Sync Complete: {profile.name}",
                        message=f"Synced {result.files_transferred} files in {result.duration_seconds}s",
                        urgency="normal",
                        icon="emblem-default",
                    )

                self._sync_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in queue worker: %s", e)
                await asyncio.sleep(1.0)

    async def handle_ipc_command(self, msg: Dict[str, Any]) -> Any:
        """Handle request messages received over Unix Domain Socket."""
        action = msg.get("action")

        if action == "get_state":
            return {
                "uptime_seconds": round(time.time() - self.start_time, 1),
                "profiles": [p.model_dump() for p in self.profiles],
                "statuses": {k: v.value for k, v in self.profile_statuses.items()},
                "current_sync_profile_id": self._current_profile_id,
                "current_progress": (
                    self._current_progress.model_dump() if self._current_progress else None
                ),
            }

        elif action == "sync_now":
            profile_id = msg.get("profile_id")
            dry_run = bool(msg.get("dry_run", False))
            resync = bool(msg.get("resync", False))
            if not profile_id:
                raise ValueError("profile_id is required")
            ok = self.trigger_sync(profile_id, source="user", dry_run=dry_run, resync=resync)
            return {"queued": ok}

        elif action == "reload_profiles":
            self.reload_profiles()
            return {"status": "ok", "profile_count": len(self.profiles)}

        elif action == "get_history":
            limit = int(msg.get("limit", 50))
            history = load_history(limit=limit)
            return [h.model_dump() for h in history]

        elif action == "stop_daemon":
            asyncio.create_task(self.stop())
            return {"status": "stopping"}

        else:
            raise ValueError(f"Unknown action: {action}")

    def reload_profiles(self) -> None:
        """Reload profile definitions from config files."""
        logger.info("Reloading profiles from disk...")
        self.profiles = load_profiles()
        self._init_statuses()

        if self.watcher_manager:
            self.watcher_manager.update_profiles(self.profiles)
        if self.scheduler:
            self.scheduler.set_profiles(self.profiles)

    async def stop(self) -> None:
        """Gracefully stop all services."""
        logger.info("Stopping Rclone AutoSync Daemon...")
        self._running = False

        if self._worker_task:
            self._worker_task.cancel()

        if self._current_executor:
            self._current_executor.cancel()

        if self.watcher_manager:
            self.watcher_manager.stop()

        if self.scheduler:
            self.scheduler.stop()

        if self.ipc_server:
            await self.ipc_server.stop()

        logger.info("Daemon cleanly terminated.")


async def run_daemon() -> None:
    """Main daemon async entry point with signal handling."""
    service = DaemonService()
    loop = asyncio.get_running_loop()

    stop_event = asyncio.Event()

    def handle_signal():
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    await service.start()
    await stop_event.wait()
    await service.stop()
