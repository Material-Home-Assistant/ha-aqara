"""Camera platform for Aqara Camera G100 (100% local, single-connection)."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
import logging
import struct
import zlib
from typing import Any

from homeassistant.components import ffmpeg
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

try:
    from homeassistant.components.camera import CameraCapabilities, StreamType
except ImportError:  # pragma: no cover - legacy HA fallback
    CameraCapabilities = None  # type: ignore[assignment]
    StreamType = None  # type: ignore[assignment]

from .const import (
    DOMAIN,
    MAX_CAMERA_CONNECTIONS,
    STREAM_ENGINE_HLS,
    STREAM_ENGINE_WEBRTC,
)
from .coordinator import AqaraG100DataUpdateCoordinator
from .entity import AqaraG100Entity

_LOGGER = logging.getLogger(__name__)


def _build_solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Generate a tiny valid PNG image locally in memory without Pillow."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        body = chunk_type + data
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return struct.pack("!I", len(data)) + body + struct.pack("!I", crc)

    r, g, b = rgb
    row = b"\x00" + bytes((r, g, b)) * width
    raw_pixels = row * height
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw_pixels, level=9))
        + _chunk(b"IEND", b"")
    )


_PRIVACY_PNG = _build_solid_png(320, 180, (16, 18, 24))
_IDLE_PNG = _build_solid_png(320, 180, (24, 32, 42))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Aqara G100 camera entity."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AqaraG100Camera(coordinator)])


class AqaraG100Camera(AqaraG100Entity, Camera):
    """Representation of an Aqara Camera G100 using the built-in RTSP Gatekeeper."""

    _attr_name = None
    _attr_supported_features = CameraEntityFeature.ON_OFF | CameraEntityFeature.STREAM
    _attr_use_stream_for_stills = False

    def __init__(self, coordinator: AqaraG100DataUpdateCoordinator) -> None:
        """Initialize the Aqara G100 camera entity."""
        AqaraG100Entity.__init__(self, coordinator, "camera")
        Camera.__init__(self)
        # Force TCP interleaved RTSP so all RTP packets stay inside the single
        # gatekeeper connection (zero UDP leaks, instant disconnect, low latency).
        self.stream_options["rtsp_transport"] = "tcp"
        self._last_snapshot: bytes | None = None

    async def async_added_to_hass(self) -> None:
        """Register stream worker stop and provider refresh hooks."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.register_stream_stop_callback(
                self._async_stop_stream_worker
            )
        )
        self.async_on_remove(
            self.coordinator.register_provider_refresh_callback(
                self._async_refresh_stream_engine
            )
        )

    async def _async_refresh_stream_engine(self) -> None:
        """Refresh WebRTC/HLS capabilities when user switches stream engine."""
        refresh_fn = getattr(self, "async_refresh_providers", None)
        if refresh_fn is not None and self.hass is not None:
            try:
                await refresh_fn(write_state=True)
                return
            except TypeError:
                await refresh_fn()
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def camera_capabilities(self) -> Any:
        """Expose HLS or HA built-in WebRTC (go2rtc) based on user engine selection."""
        caps = super().camera_capabilities
        if CameraCapabilities is None or StreamType is None:
            return caps

        engine = (
            self.coordinator.data.stream_engine
            if self.coordinator.data is not None
            else self.coordinator.stream_engine
        )
        stream_types = set(getattr(caps, "frontend_stream_types", ()))
        if engine == STREAM_ENGINE_HLS:
            new_types = {StreamType.HLS}
        elif engine == STREAM_ENGINE_WEBRTC and StreamType.WEB_RTC in stream_types:
            new_types = {StreamType.WEB_RTC}
        else:
            return caps

        try:
            return replace(caps, frontend_stream_types=new_types)
        except TypeError:
            return CameraCapabilities(frontend_stream_types=new_types)

    async def _async_stop_stream_worker(self) -> None:
        """Stop Home Assistant's internal PyAV stream worker if active."""
        if self.stream is not None:
            with suppress(Exception):
                await self.stream.stop()
            self.stream = None

    @property
    def available(self) -> bool:
        """Keep entity available while Privacy Mode is active so user can wake it."""
        if self.coordinator.privacy_mode:
            return True
        return super().available

    @property
    def is_on(self) -> bool:
        """Return True when Privacy Mode is disabled."""
        if self.coordinator.data is not None:
            return not self.coordinator.data.privacy_mode
        return not self.coordinator.privacy_mode

    @property
    def is_streaming(self) -> bool:
        """Return True when the built-in gatekeeper is receiving live stream data."""
        if self.coordinator.data is not None:
            return self.coordinator.data.is_transmitting
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose built-in gatekeeper, stream engine, and single-connection metrics."""
        data = self.coordinator.data
        telemetry = self.coordinator.client.gatekeeper.get_telemetry()
        webrtc_available = False
        if StreamType is not None:
            with suppress(Exception):
                base_caps = super().camera_capabilities
                webrtc_available = StreamType.WEB_RTC in set(
                    getattr(base_caps, "frontend_stream_types", ())
                )
        return {
            "active_channel": (
                data.active_channel if data else self.coordinator.active_channel
            ),
            "stream_engine": (
                data.stream_engine if data else self.coordinator.stream_engine
            ),
            "webrtc_provider_available": webrtc_available,
            "privacy_mode": (
                data.privacy_mode if data else self.coordinator.privacy_mode
            ),
            "idle_transmission_guard": (
                data.idle_transmission_guard
                if data
                else self.coordinator.idle_transmission_guard
            ),
            "active_connections": telemetry.active_connections,
            "max_connections": MAX_CAMERA_CONNECTIONS,
            "blocked_connections": telemetry.blocked_connections,
            "bitrate_kbps": telemetry.bitrate_kbps,
            "gatekeeper_port": telemetry.listen_port,
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still frame without opening a second connection to the G100."""
        data = self.coordinator.data
        privacy_active = data.privacy_mode if data else self.coordinator.privacy_mode
        if privacy_active:
            return _PRIVACY_PNG

        # 1. If HA's single live stream worker is already running, grab the frame
        # directly from memory/PyAV without opening another RTSP connection.
        if (
            self.stream is not None
            and getattr(self.stream, "available", False)
            and hasattr(self.stream, "async_get_image")
        ):
            with suppress(Exception):
                image = await self.stream.async_get_image(width=width, height=height)
                if image:
                    self._last_snapshot = image
                    return image

        idle_guard = (
            data.idle_transmission_guard
            if data
            else self.coordinator.idle_transmission_guard
        )
        is_transmitting = data.is_transmitting if data else False

        # 2. When Idle Transmission Guard is active and no viewer is streaming,
        # never wake the G100 RTSP encoder for background dashboard polling.
        if idle_guard and not is_transmitting:
            return self._last_snapshot or _IDLE_PNG

        # 3. If another consumer holds the single camera slot, avoid spawning a
        # competing ffmpeg process.
        telemetry = self.coordinator.client.gatekeeper.get_telemetry()
        if telemetry.active_connections >= MAX_CAMERA_CONNECTIONS:
            return self._last_snapshot or _IDLE_PNG

        # 4. Otherwise capture one frame through the built-in gatekeeper.
        local_url = self.coordinator.client.gatekeeper.local_rtsp_url(
            self.coordinator.active_channel
        )
        with suppress(Exception):
            frame = await ffmpeg.async_get_image(
                self.hass,
                local_url,
                width=width,
                height=height,
                extra_cmd="-rtsp_transport tcp",
            )
            if frame:
                self._last_snapshot = frame
                return frame

        return self._last_snapshot or _IDLE_PNG

    async def stream_source(self) -> str | None:
        """Return the built-in single-connection gatekeeper RTSP URL."""
        if self.coordinator.privacy_mode:
            return None
        gatekeeper = self.coordinator.client.gatekeeper
        if not gatekeeper.is_running:
            return None
        return gatekeeper.local_rtsp_url(self.coordinator.active_channel)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off camera by activating Privacy Mode and disconnecting stream."""
        await self.coordinator.async_set_privacy_mode(True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on camera by disabling Privacy Mode."""
        await self.coordinator.async_set_privacy_mode(False)
