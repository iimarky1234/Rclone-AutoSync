"""Formatters for sizes, speeds, and timestamps in TUI."""

from __future__ import annotations

import datetime


def format_bytes(num_bytes: int | float) -> str:
    """Format bytes into human-readable string (e.g. 1.25 MB)."""
    if num_bytes is None:
        return "0 B"
    n = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024.0:
            return f"{n:3.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} PB"


def format_speed(bytes_per_sec: float) -> str:
    """Format speed into human-readable string (e.g. 4.2 MB/s)."""
    return f"{format_bytes(bytes_per_sec)}/s"


def format_duration(seconds: int | float | None) -> str:
    """Format duration in seconds into human-readable MM:SS or HH:MM:SS."""
    if seconds is None or seconds < 0:
        return "--:--"
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_iso_timestamp(iso_str: str | None) -> str:
    """Format ISO timestamp into friendly local time string."""
    if not iso_str:
        return "Never"
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        # Convert to local time if timezone is present
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str
