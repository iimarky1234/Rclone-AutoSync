"""Rclone execution engine with real-time JSON stats parsing."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import AsyncGenerator, Callable, List, Optional

from ..config import load_global_config
from ..models import (
    ActiveFileTransfer,
    GlobalConfig,
    SyncMode,
    SyncProfile,
    SyncRunResult,
    TransferProgress,
)

logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean_ansi(text: str) -> str:
    """Strip ANSI escape sequences from strings."""
    if not text:
        return ""
    return ANSI_ESCAPE.sub("", text).strip()


def has_bisync_listings(profile: SyncProfile) -> bool:
    """Check if prior bisync listing files exist in rclone cache."""
    cache_dir = Path.home() / ".cache" / "rclone" / "bisync"
    if not cache_dir.exists():
        return False

    clean_local = str(profile.resolved_local_path).strip("/").replace("/", "_")
    clean_remote = profile.remote.rstrip(":")

    for f in cache_dir.glob("*.lst"):
        if clean_local in f.name and clean_remote in f.name:
            return True
    return False


def build_rclone_cmd(
    profile: SyncProfile,
    config: Optional[GlobalConfig] = None,
    dry_run: bool = False,
    resync: bool = False,
    extra_flags: Optional[List[str]] = None,
) -> List[str]:
    """Construct the rclone command line for a sync profile."""
    if config is None:
        config = load_global_config()

    rclone_bin = config.rclone_path or "rclone"
    cmd = [rclone_bin, profile.mode.value]

    # Source and Destination
    cmd.append(str(profile.resolved_local_path))
    cmd.append(profile.full_remote_target)

    # Core logging & stats output
    cmd.extend([
        "--use-json-log",
        "--stats", "1s",
        "--stats-log-level", "NOTICE",
        "--stats-one-line=false",
        "--transfers", str(config.transfers),
        "--checkers", str(config.checkers),
        "-v",
    ])

    # Bandwidth limit: Profile takes precedence over global default
    bwlimit = profile.bwlimit or config.default_bwlimit
    if bwlimit and bwlimit.strip() and bwlimit.strip() != "0":
        cmd.extend(["--bwlimit", bwlimit.strip()])

    # Google Drive trash safety
    if profile.use_trash:
        cmd.append("--drive-use-trash")

    # Exclude patterns
    for pattern in profile.excludes:
        if pattern.strip():
            cmd.extend(["--exclude", pattern.strip()])

    # Dry-run preview
    if dry_run:
        cmd.append("--dry-run")

    # Bisync special flags
    if profile.mode == SyncMode.BISYNC:
        cmd.extend(["--verbose", "--resilient", "--recover"])
        # If explicitly requested or if no prior listings exist, use --resync
        if resync or not has_bisync_listings(profile):
            cmd.append("--resync")

    # Profile custom extra flags
    if profile.extra_flags:
        cmd.extend(profile.extra_flags)

    # Ad-hoc extra flags
    if extra_flags:
        cmd.extend(extra_flags)

    return cmd


class RcloneExecutor:
    """Executes rclone commands and streams parsed real-time progress and logs."""

    def __init__(self, profile: SyncProfile, config: Optional[GlobalConfig] = None):
        self.profile = profile
        self.config = config or load_global_config()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._cancelled = False

    async def run(
        self,
        dry_run: bool = False,
        resync: bool = False,
        on_progress: Optional[Callable[[TransferProgress], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        extra_flags: Optional[List[str]] = None,
    ) -> SyncRunResult:
        """Run the sync operation for the profile."""
        cmd = build_rclone_cmd(
            self.profile,
            self.config,
            dry_run=dry_run,
            resync=resync,
            extra_flags=extra_flags,
        )
        logger.info("Executing rclone: %s", " ".join(cmd))

        start_time = time.time()
        action_log: List[str] = []
        last_progress = TransferProgress(
            profile_id=self.profile.id,
            profile_name=self.profile.name,
            dry_run=dry_run,
        )

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # In rclone with --use-json-log, logs and stats are sent to stderr
            # We concurrently read both stdout and stderr
            async def read_stream(
                stream: asyncio.StreamReader,
                is_stderr: bool = True
            ) -> None:
                nonlocal last_progress
                while True:
                    line_bytes = await stream.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode(errors="replace").strip()
                    if not line:
                        continue

                    if line.startswith("{") and line.endswith("}"):
                        try:
                            data = json.loads(line)
                            level = data.get("level", "info")
                            msg = data.get("msg", "")

                            # Handle stats JSON
                            if "stats" in data:
                                stats_data = data["stats"]
                                total_bytes = stats_data.get("totalBytes", 0)
                                bytes_done = stats_data.get("bytes", 0)
                                pct = (
                                    (bytes_done / total_bytes * 100.0)
                                    if total_bytes > 0
                                    else 100.0 if bytes_done > 0 else 0.0
                                )

                                # Active file transfers
                                active_files: List[ActiveFileTransfer] = []
                                for item in stats_data.get("transferring", []) or []:
                                    active_files.append(
                                        ActiveFileTransfer(
                                            name=item.get("name", "Unknown"),
                                            size=item.get("size", 0),
                                            bytes=item.get("bytes", 0),
                                            percentage=item.get("percentage", 0),
                                            speed=float(item.get("speed", 0.0)),
                                            eta=item.get("eta"),
                                        )
                                    )

                                last_progress = TransferProgress(
                                    profile_id=self.profile.id,
                                    profile_name=self.profile.name,
                                    status="syncing",
                                    dry_run=dry_run,
                                    bytes_transferred=bytes_done,
                                    total_bytes=total_bytes,
                                    percentage=round(pct, 1),
                                    speed=float(stats_data.get("speed", 0.0)),
                                    eta_seconds=stats_data.get("eta"),
                                    transfers=stats_data.get("transfers", 0),
                                    total_transfers=stats_data.get("totalTransfers", 0),
                                    deletes=stats_data.get("deletes", 0),
                                    checks=stats_data.get("checks", 0),
                                    total_checks=stats_data.get("totalChecks", 0),
                                    elapsed_time=round(float(stats_data.get("elapsedTime", 0.0)), 1),
                                    errors=stats_data.get("errors", 0),
                                    active_files=active_files,
                                    current_message=msg.strip(),
                                )

                                if on_progress:
                                    on_progress(last_progress)

                            else:
                                # Normal json log entry
                                formatted = f"[{level.upper()}] {msg}"
                                if "object" in data:
                                    formatted += f" ({data['object']})"
                                action_log.append(formatted)
                                if on_log:
                                    on_log(level, formatted)

                                # Update live status message for UI progress card
                                clean_msg = clean_ansi(msg).strip()
                                if clean_msg and on_progress:
                                    obj_str = f" ({data['object']})" if "object" in data else ""
                                    last_progress.current_message = f"{clean_msg}{obj_str}"
                                    on_progress(last_progress)

                        except json.JSONDecodeError:
                            action_log.append(line)
                            if on_log:
                                on_log("info", line)
                    else:
                        action_log.append(line)
                        if on_log:
                            on_log("info", line)

            assert self._proc.stdout is not None
            assert self._proc.stderr is not None
            await asyncio.gather(
                read_stream(self._proc.stdout, is_stderr=False),
                read_stream(self._proc.stderr, is_stderr=True),
            )

            await self._proc.wait()
            return_code = self._proc.returncode

        except asyncio.CancelledError:
            self._cancelled = True
            if self._proc:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
            raise

        except Exception as e:
            return_code = -1
            action_log.append(f"[ERROR] Exception during rclone execution: {e}")

        duration = round(time.time() - start_time, 2)
        success = (return_code == 0) and not self._cancelled

        error_message = None
        if not success:
            error_lines = [l for l in action_log if "error" in l.lower() or "fatal" in l.lower()]
            error_message = clean_ansi(error_lines[-1]) if error_lines else f"Exited with code {return_code}"

        return SyncRunResult(
            profile_id=self.profile.id,
            profile_name=self.profile.name,
            success=success,
            dry_run=dry_run,
            duration_seconds=duration,
            bytes_transferred=last_progress.bytes_transferred,
            files_transferred=last_progress.transfers,
            files_deleted=last_progress.deletes,
            files_checked=last_progress.checks,
            error_message=error_message,
            action_log=action_log[-100:],  # preserve last 100 log lines
        )

    def cancel(self) -> None:
        """Cancel ongoing execution."""
        self._cancelled = True
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
