"""Interval-based scheduler for periodic sync execution."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

from ..models import SyncProfile

logger = logging.getLogger(__name__)


class IntervalScheduler:
    """Schedules recurring sync tasks for profiles configured with intervals."""

    def __init__(self, trigger_callback: Callable[[str, str], None]):
        self.trigger_callback = trigger_callback
        self._last_run: Dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._profiles: List[SyncProfile] = []

    def set_profiles(self, profiles: List[SyncProfile]) -> None:
        self._profiles = profiles

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())
            logger.info("Interval scheduler started")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Interval scheduler stopped")

    async def _run_loop(self) -> None:
        while True:
            try:
                now = time.time()
                for profile in self._profiles:
                    if not profile.enabled or not profile.schedule_interval_minutes:
                        continue

                    interval_secs = profile.schedule_interval_minutes * 60
                    last_time = self._last_run.get(profile.id, 0.0)

                    if now - last_time >= interval_secs:
                        self._last_run[profile.id] = now
                        logger.info(
                            "Interval elapsed for profile '%s' (%dm). Triggering sync.",
                            profile.name,
                            profile.schedule_interval_minutes,
                        )
                        self.trigger_callback(profile.id, "schedule")

                await asyncio.sleep(30.0)  # check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in scheduler loop: %s", e)
                await asyncio.sleep(10.0)
