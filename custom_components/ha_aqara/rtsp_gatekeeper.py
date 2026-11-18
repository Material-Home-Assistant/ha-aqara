"""Built-in single-connection RTSP Gatekeeper/Proxy for Aqara Camera G100."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
import logging
import re
import socket
import time
from urllib.parse import quote

from .const import DEFAULT_RTSP_PORT, MAX_CAMERA_CONNECTIONS

_LOGGER = logging.getLogger(__name__)
_RELAY_CHUNK_SIZE = 65536
_CSEQ_RE = re.compile(rb"CSeq:\s*(\d+)", re.IGNORECASE)


def _enable_tcp_nodelay(writer: asyncio.StreamWriter) -> None:
    """Disable Nagle buffering on a stream socket for minimum video latency."""
    sock = writer.get_extra_info("socket")
    if sock is not None:
        with suppress(OSError):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


@dataclass(frozen=True, slots=True)
class GatekeeperTelemetry:
    """Live telemetry snapshot from the built-in RTSP Gatekeeper."""

    running: bool
    listen_port: int
    active_connections: int
    max_connections: int
    is_transmitting: bool
    bytes_received: int
    bitrate_kbps: float
    blocked_connections: int


class AqaraG100SingleConnGatekeeper:
    """Local TCP/RTSP gatekeeper limiting camera connections to strictly 1.

    All Home Assistant video/snapshot requests connect to 127.0.0.1:<listen_port>.
    The gatekeeper guarantees that at most 1 upstream TCP socket is ever open
    to the Aqara G100 camera_ip:rtsp_port, provides zero-latency TCP_NODELAY
    forwarding, tracks live bytes/bitrate, and supports instant termination when
    Privacy Mode is activated.
    """

    def __init__(
        self,
        camera_ip: str,
        rtsp_port: int = DEFAULT_RTSP_PORT,
        rtsp_username: str = "",
        rtsp_password: str = "",
        max_connections: int = MAX_CAMERA_CONNECTIONS,
        stale_timeout: float = 8.0,
        on_state_change: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the gatekeeper."""
        self.camera_ip = camera_ip.strip()
        self.rtsp_port = rtsp_port
        self.rtsp_username = rtsp_username
        self.rtsp_password = rtsp_password
        self.max_connections = max(1, max_connections)
        self.stale_timeout = stale_timeout
        self._on_state_change = on_state_change

        self._server: asyncio.AbstractServer | None = None
        self.listen_port: int = 0
        self._lock = asyncio.Lock()
        self._privacy_mode = False
        self._connecting = False

        self._active_connections = 0
        self._blocked_connections = 0
        self._bytes_received = 0
        self._is_transmitting = False
        self._bitrate_kbps = 0.0
        self._last_activity_ts = 0.0
        self._last_upstream_ts = 0.0
        self._rate_window_start = 0.0
        self._rate_window_bytes = 0

        self._active_upstream_writer: asyncio.StreamWriter | None = None
        self._active_downstream_writer: asyncio.StreamWriter | None = None

    @property
    def is_running(self) -> bool:
        """Return True if the local gatekeeper TCP listener is active."""
        return self._server is not None and self._server.is_serving()

    def set_on_state_change(self, callback: Callable[[], None] | None) -> None:
        """Register or replace state-change callback."""
        self._on_state_change = callback

    def update_target(
        self,
        camera_ip: str,
        rtsp_port: int,
        rtsp_username: str,
        rtsp_password: str,
    ) -> None:
        """Update upstream camera connection target."""
        self.camera_ip = camera_ip.strip()
        self.rtsp_port = rtsp_port
        self.rtsp_username = rtsp_username
        self.rtsp_password = rtsp_password

    def local_rtsp_url(self, channel: str) -> str:
        """Build the local gatekeeper RTSP URL for Home Assistant stream/ffmpeg."""
        clean_channel = channel.lstrip("/")
        if self.rtsp_username or self.rtsp_password:
            user = quote(self.rtsp_username, safe="")
            password = quote(self.rtsp_password, safe="")
            return f"rtsp://{user}:{password}@127.0.0.1:{self.listen_port}/{clean_channel}"
        return f"rtsp://127.0.0.1:{self.listen_port}/{clean_channel}"

    def direct_rtsp_url(self, channel: str) -> str:
        """Return the direct upstream camera RTSP URL (for diagnostics only)."""
        clean_channel = channel.lstrip("/")
        if self.rtsp_username or self.rtsp_password:
            user = quote(self.rtsp_username, safe="")
            password = quote(self.rtsp_password, safe="")
            return (
                f"rtsp://{user}:{password}@{self.camera_ip}:"
                f"{self.rtsp_port}/{clean_channel}"
            )
        return f"rtsp://{self.camera_ip}:{self.rtsp_port}/{clean_channel}"

    def get_telemetry(self) -> GatekeeperTelemetry:
        """Return current connection, transmission, and bitrate telemetry."""
        now = time.monotonic()
        active_now = self._active_connections
        transmitting_now = (
            active_now > 0
            and self._is_transmitting
            and (now - self._last_upstream_ts) < 5.0
        )
        bitrate = self._bitrate_kbps if transmitting_now else 0.0
        return GatekeeperTelemetry(
            running=self.is_running,
            listen_port=self.listen_port,
            active_connections=active_now,
            max_connections=self.max_connections,
            is_transmitting=transmitting_now,
            bytes_received=self._bytes_received,
            bitrate_kbps=round(bitrate, 1),
            blocked_connections=self._blocked_connections,
        )

    def _notify_state_change(self) -> None:
        """Notify coordinator listener safely."""
        if self._on_state_change is not None:
            with suppress(Exception):
                self._on_state_change()

    async def async_start(self) -> int:
        """Start the local gatekeeper server on 127.0.0.1 and return its port."""
        if self._server is not None:
            return self.listen_port

        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
        )
        sockets = self._server.sockets or []
        if sockets:
            self.listen_port = int(sockets[0].getsockname()[1])
        _LOGGER.debug(
            "Aqara G100 single-connection gatekeeper listening on 127.0.0.1:%s -> %s:%s",
            self.listen_port,
            self.camera_ip,
            self.rtsp_port,
        )
        return self.listen_port

    async def async_stop(self) -> None:
        """Close active upstream session and shut down local gatekeeper."""
        await self.async_force_disconnect()
        if self._server is not None:
            self._server.close()
            with suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        self.listen_port = 0

    async def async_set_privacy_mode(self, enabled: bool) -> None:
        """Enable or disable Privacy Mode, cutting any open connection immediately."""
        self._privacy_mode = enabled
        if enabled:
            await self.async_force_disconnect()
        else:
            self._notify_state_change()

    async def async_force_disconnect(self) -> None:
        """Forcefully close the active upstream/downstream session immediately."""
        async with self._lock:
            changed = await self._async_close_active_locked()
        if changed:
            self._notify_state_change()

    async def _async_close_active_locked(self) -> bool:
        """Close the currently tracked session (must hold self._lock)."""
        had_active = (
            self._active_connections > 0
            or self._active_upstream_writer is not None
            or self._active_downstream_writer is not None
        )
        for writer in (self._active_upstream_writer, self._active_downstream_writer):
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await asyncio.wait_for(writer.wait_closed(), timeout=0.5)

        self._active_upstream_writer = None
        self._active_downstream_writer = None
        self._active_connections = 0
        self._is_transmitting = False
        self._bitrate_kbps = 0.0
        self._rate_window_bytes = 0
        return had_active

    async def _reject_client(
        self,
        down_reader: asyncio.StreamReader,
        down_writer: asyncio.StreamWriter,
        status_line: bytes,
    ) -> None:
        """Send an RTSP error response if client sent a request, then close."""
        self._blocked_connections += 1
        with suppress(TimeoutError, OSError):
            peek = await asyncio.wait_for(down_reader.read(512), timeout=0.25)
            if b"RTSP/" in peek:
                match = _CSEQ_RE.search(peek)
                cseq = match.group(1) if match else b"1"
                down_writer.write(
                    status_line
                    + b"\r\nCSeq: "
                    + cseq
                    + b"\r\nConnection: close\r\n\r\n"
                )
                await down_writer.drain()
        down_writer.close()
        with suppress(Exception):
            await down_writer.wait_closed()
        self._notify_state_change()

    async def _handle_client(
        self,
        down_reader: asyncio.StreamReader,
        down_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming local RTSP client under strict 1-connection limit."""
        _enable_tcp_nodelay(down_writer)

        reject_status: bytes | None = None
        async with self._lock:
            if self._privacy_mode:
                reject_status = b"RTSP/1.0 403 Forbidden"
            elif self._connecting:
                reject_status = b"RTSP/1.0 503 Service Unavailable"
            elif self._active_connections >= self.max_connections:
                now = time.monotonic()
                upstream_closing = (
                    self._active_upstream_writer is None
                    or self._active_upstream_writer.is_closing()
                )
                stale = (now - self._last_activity_ts) > self.stale_timeout
                if upstream_closing or stale:
                    _LOGGER.debug(
                        "Replacing stale upstream connection to %s:%s",
                        self.camera_ip,
                        self.rtsp_port,
                    )
                    await self._async_close_active_locked()
                else:
                    _LOGGER.debug(
                        "Blocking extra concurrent connection to %s:%s (limit=%s)",
                        self.camera_ip,
                        self.rtsp_port,
                        self.max_connections,
                    )
                    reject_status = b"RTSP/1.0 503 Service Unavailable"

            if reject_status is None:
                self._connecting = True

        if reject_status is not None:
            await self._reject_client(down_reader, down_writer, reject_status)
            return

        up_reader: asyncio.StreamReader | None = None
        up_writer: asyncio.StreamWriter | None = None
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self.camera_ip, self.rtsp_port),
                timeout=4.0,
            )
        except (OSError, TimeoutError) as err:
            _LOGGER.debug(
                "Gatekeeper upstream connect failed to %s:%s: %s",
                self.camera_ip,
                self.rtsp_port,
                err,
            )
            async with self._lock:
                self._connecting = False
            await self._reject_client(
                down_reader, down_writer, b"RTSP/1.0 502 Bad Gateway"
            )
            return

        aborted_by_privacy = False
        async with self._lock:
            self._connecting = False
            if self._privacy_mode or not self.is_running:
                aborted_by_privacy = True
            else:
                _enable_tcp_nodelay(up_writer)
                now = time.monotonic()
                self._active_connections = 1
                self._active_upstream_writer = up_writer
                self._active_downstream_writer = down_writer
                self._last_activity_ts = now
                self._last_upstream_ts = 0.0
                self._rate_window_start = now
                self._rate_window_bytes = 0
                self._is_transmitting = False
                self._bitrate_kbps = 0.0

        if aborted_by_privacy:
            up_writer.close()
            with suppress(Exception):
                await up_writer.wait_closed()
            await self._reject_client(
                down_reader, down_writer, b"RTSP/1.0 403 Forbidden"
            )
            return

        self._notify_state_change()

        try:
            await self._relay_bidirectional(
                down_reader, down_writer, up_reader, up_writer
            )
        finally:
            cleaned = False
            async with self._lock:
                if self._active_downstream_writer is down_writer:
                    cleaned = await self._async_close_active_locked()
            if cleaned:
                self._notify_state_change()

    async def _relay_bidirectional(
        self,
        down_reader: asyncio.StreamReader,
        down_writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
    ) -> None:
        """Forward bytes in both directions until either side closes."""

        async def _pump(
            src: asyncio.StreamReader,
            dst: asyncio.StreamWriter,
            *,
            count_upstream: bool,
        ) -> None:
            session_bytes = 0
            while True:
                data = await src.read(_RELAY_CHUNK_SIZE)
                if not data:
                    break
                dst.write(data)
                await dst.drain()

                now = time.monotonic()
                self._last_activity_ts = now
                if count_upstream:
                    self._last_upstream_ts = now
                    session_bytes += len(data)
                    self._bytes_received += len(data)
                    self._rate_window_bytes += len(data)
                    was_transmitting = self._is_transmitting
                    if session_bytes >= 8192 or data.lstrip().startswith(b"$"):
                        self._is_transmitting = True
                    elapsed = now - self._rate_window_start
                    if elapsed >= 1.0:
                        self._bitrate_kbps = (
                            self._rate_window_bytes * 8.0 / 1000.0
                        ) / elapsed
                        self._rate_window_start = now
                        self._rate_window_bytes = 0
                    if self._is_transmitting and not was_transmitting:
                        self._notify_state_change()

        tasks = [
            asyncio.create_task(_pump(down_reader, up_writer, count_upstream=False)),
            asyncio.create_task(_pump(up_reader, down_writer, count_upstream=True)),
        ]
        try:
            _, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            raise
        except (OSError, ConnectionError):
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
