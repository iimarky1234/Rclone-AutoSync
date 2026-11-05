"""Unix domain socket IPC protocol for daemon and TUI/CLI communication."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set

from ..config import get_socket_path

logger = logging.getLogger(__name__)


class IPCServer:
    """Unix Domain Socket server for receiving commands and broadcasting events."""

    def __init__(
        self,
        handler_callback: Callable[[Dict[str, Any]], asyncio.Future | Any],
        socket_path: Optional[Path] = None,
    ):
        self.handler_callback = handler_callback
        self.socket_path = socket_path or get_socket_path()
        self._server: Optional[asyncio.Server] = None
        self._subscribers: Set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        """Start listening on the Unix domain socket."""
        if self.socket_path.exists():
            try:
                # Test if another daemon is already actively listening
                _, writer = await asyncio.open_unix_connection(str(self.socket_path))
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(f"Another daemon is already listening on {self.socket_path}")
            except (ConnectionRefusedError, FileNotFoundError):
                # Stale socket file, remove it
                self.socket_path.unlink(missing_ok=True)

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        logger.info("IPC Server listening on %s", self.socket_path)

    async def stop(self) -> None:
        """Stop server and clean up socket file."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for writer in list(self._subscribers):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._subscribers.clear()

        if self.socket_path.exists():
            self.socket_path.unlink(missing_ok=True)
        logger.info("IPC Server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                raw = line.decode().strip()
                if not raw:
                    continue

                try:
                    msg = json.loads(raw)
                    action = msg.get("action")
                    msg_id = msg.get("id")

                    if action == "subscribe":
                        self._subscribers.add(writer)
                        resp = {"id": msg_id, "status": "ok", "message": "Subscribed to events"}
                        writer.write((json.dumps(resp) + "\n").encode())
                        await writer.drain()
                        continue

                    # Delegate action to callback
                    result = self.handler_callback(msg)
                    if asyncio.iscoroutine(result):
                        result = await result

                    response = {"id": msg_id, "status": "ok", "data": result}
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()

                except json.JSONDecodeError:
                    err_resp = {"status": "error", "message": "Invalid JSON"}
                    writer.write((json.dumps(err_resp) + "\n").encode())
                    await writer.drain()
                except Exception as e:
                    err_resp = {"status": "error", "message": str(e)}
                    writer.write((json.dumps(err_resp) + "\n").encode())
                    await writer.drain()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("IPC client connection error: %s", e)
        finally:
            self._subscribers.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def broadcast_event(self, event_type: str, data: Any) -> None:
        """Broadcast an event to all subscribed clients."""
        if not self._subscribers:
            return

        payload = json.dumps({"event": event_type, "data": data}) + "\n"
        dead: List[asyncio.StreamWriter] = []

        for writer in self._subscribers:
            try:
                writer.write(payload.encode())
                await writer.drain()
            except Exception:
                dead.append(writer)

        for w in dead:
            self._subscribers.discard(w)


class DaemonClient:
    """Client for communicating with the rasync background daemon."""

    def __init__(self, socket_path: Optional[Path] = None):
        self.socket_path = socket_path or get_socket_path()

    async def is_running(self) -> bool:
        """Check if daemon socket is reachable."""
        if not self.socket_path.exists():
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def send_command(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        """Send a single request-response command to the daemon."""
        if not self.socket_path.exists():
            raise ConnectionError(f"Daemon socket not found at {self.socket_path}")

        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        try:
            payload = {"action": action, **kwargs}
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line:
                raise ConnectionError("Daemon closed connection without response")
            return json.loads(line.decode().strip())
        finally:
            writer.close()
            await writer.wait_closed()

    async def subscribe_events(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Subscribe to daemon events and yield them as received."""
        if not self.socket_path.exists():
            raise ConnectionError("Daemon socket not found")

        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        try:
            writer.write((json.dumps({"action": "subscribe"}) + "\n").encode())
            await writer.drain()

            # First response is subscription acknowledgement
            await reader.readline()

            while True:
                line = await reader.readline()
                if not line:
                    break
                data = json.loads(line.decode().strip())
                if "event" in data:
                    yield data
        finally:
            writer.close()
            await writer.wait_closed()
