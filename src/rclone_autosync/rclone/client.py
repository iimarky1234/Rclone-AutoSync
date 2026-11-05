"""Rclone discovery and query operations."""

from __future__ import annotations

import asyncio
import shutil
from typing import List, Optional, Tuple


def is_rclone_available(rclone_bin: str = "rclone") -> bool:
    """Check if the rclone binary is available in PATH or specified location."""
    return shutil.which(rclone_bin) is not None


async def get_rclone_version(rclone_bin: str = "rclone") -> Optional[str]:
    """Retrieve rclone version string."""
    try:
        proc = await asyncio.create_subprocess_exec(
            rclone_bin,
            "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            first_line = stdout.decode().strip().split("\n")[0]
            return first_line
    except Exception:
        pass
    return None


async def list_remotes(rclone_bin: str = "rclone") -> List[str]:
    """List configured rclone remotes (names stripped of trailing colon)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            rclone_bin,
            "listremotes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            lines = stdout.decode().splitlines()
            return [line.strip().rstrip(":") for line in lines if line.strip()]
    except Exception:
        pass
    return []


async def get_remote_type(remote: str, rclone_bin: str = "rclone") -> Optional[str]:
    """Get the remote type (e.g. drive, s3, etc.) for a remote."""
    clean_remote = remote.rstrip(":")
    try:
        proc = await asyncio.create_subprocess_exec(
            rclone_bin,
            "config",
            "show",
            clean_remote,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            for line in stdout.decode().splitlines():
                if line.strip().startswith("type ="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


async def list_remote_dirs(
    remote: str,
    path: str = "",
    max_depth: int = 1,
    rclone_bin: str = "rclone",
) -> List[str]:
    """List subdirectories in a given remote path using `rclone lsf --dirs-only`."""
    clean_remote = remote.rstrip(":")
    clean_path = path.strip("/")
    target = f"{clean_remote}:{clean_path}" if clean_path else f"{clean_remote}:"

    args = [
        rclone_bin,
        "lsf",
        "--dirs-only",
        f"--max-depth={max_depth}",
        target,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            dirs = [d.strip().rstrip("/") for d in stdout.decode().splitlines() if d.strip()]
            dirs.sort()
            return dirs
    except Exception:
        pass
    return []
