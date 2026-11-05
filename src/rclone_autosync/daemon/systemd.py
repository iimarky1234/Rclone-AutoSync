"""Systemd user service integration for rasync."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple

SERVICE_NAME = "rasync.service"
USER_SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"


def get_service_file_path() -> Path:
    return USER_SYSTEMD_DIR / SERVICE_NAME


def get_rasync_executable() -> str:
    """Find the path to the rasync executable or fall back to sys.executable with -m."""
    which_rasync = shutil.which("rasync")
    if which_rasync:
        return which_rasync
    # Fallback to python -m rclone_autosync.cli
    return f"{sys.executable} -m rclone_autosync.cli"


def generate_service_unit_content(exec_path: Optional[str] = None) -> str:
    """Generate systemd service unit definition."""
    executable = exec_path or get_rasync_executable()
    # If executable contains spaces and is python command, format properly
    exec_start = f"{executable} daemon"

    return f"""[Unit]
Description=Rclone AutoSync Daemon
Documentation=https://github.com/iimarky1234/rclone-autosync
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


async def run_systemctl(*args: str) -> Tuple[int, str, str]:
    """Run a systemctl --user command."""
    cmd = ["systemctl", "--user", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )
    except Exception as e:
        return (-1, "", str(e))


async def is_service_installed() -> bool:
    return get_service_file_path().exists()


async def is_service_active() -> bool:
    code, out, _ = await run_systemctl("is-active", SERVICE_NAME)
    return code == 0 and out == "active"


async def is_service_enabled() -> bool:
    code, out, _ = await run_systemctl("is-enabled", SERVICE_NAME)
    return code == 0 and out == "enabled"


async def get_service_status_summary() -> str:
    """Get high-level status string e.g. 'active (running)' or 'inactive'."""
    code, out, err = await run_systemctl("is-active", SERVICE_NAME)
    if code == 0:
        return "active (running)"
    if not await is_service_installed():
        return "not installed"
    return out or "inactive"


async def install_service(start_now: bool = True) -> Tuple[bool, str]:
    """Write the systemd service file, reload daemon, and optionally enable/start."""
    USER_SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    service_path = get_service_file_path()
    content = generate_service_unit_content()

    service_path.write_text(content, encoding="utf-8")

    code, _, err = await run_systemctl("daemon-reload")
    if code != 0:
        return False, f"Failed daemon-reload: {err}"

    if start_now:
        code, _, err = await run_systemctl("enable", "--now", SERVICE_NAME)
        if code != 0:
            return False, f"Failed to enable & start service: {err}"

    return True, "Service successfully installed and enabled"


async def uninstall_service() -> Tuple[bool, str]:
    """Stop, disable, and remove the systemd user service."""
    await run_systemctl("stop", SERVICE_NAME)
    await run_systemctl("disable", SERVICE_NAME)

    service_path = get_service_file_path()
    if service_path.exists():
        service_path.unlink()

    await run_systemctl("daemon-reload")
    return True, "Service successfully uninstalled"


async def start_service() -> Tuple[bool, str]:
    code, out, err = await run_systemctl("start", SERVICE_NAME)
    return (code == 0, err if code != 0 else "Service started")


async def stop_service() -> Tuple[bool, str]:
    code, out, err = await run_systemctl("stop", SERVICE_NAME)
    return (code == 0, err if code != 0 else "Service stopped")


async def restart_service() -> Tuple[bool, str]:
    code, out, err = await run_systemctl("restart", SERVICE_NAME)
    return (code == 0, err if code != 0 else "Service restarted")


async def get_recent_journal_logs(lines: int = 50) -> str:
    """Fetch recent journalctl logs for rasync.service."""
    cmd = ["journalctl", "--user-unit", SERVICE_NAME, "-n", str(lines), "--no-pager"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="replace")
    except Exception as e:
        return f"Failed to read logs: {e}"
