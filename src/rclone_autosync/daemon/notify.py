"""Desktop notification utilities."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Optional

from ..config import load_global_config

logger = logging.getLogger(__name__)


async def send_notification(
    title: str,
    message: str,
    urgency: str = "normal",  # low, normal, critical
    icon: Optional[str] = "drive-harddisk",
) -> bool:
    """Send a Linux desktop notification using notify-send if enabled."""
    config = load_global_config()
    if not config.enable_desktop_notifications:
        return False

    notify_bin = shutil.which("notify-send")
    if not notify_bin:
        return False

    args = [
        notify_bin,
        "--app-name=rclone-autosync",
        f"--urgency={urgency}",
    ]
    if icon:
        args.extend(["--icon", icon])
    args.extend([title, message])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception as e:
        logger.debug("Failed to send desktop notification: %s", e)
        return False
