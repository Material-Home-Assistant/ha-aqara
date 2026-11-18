"""DataUpdateCoordinator for the Aqara Camera G100 integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CHANNEL_OPTIONS,
    CONF_DEFAULT_CHANNEL,
    CONF_IDLE_SLEEP_SNAPSHOT,
    CONF_STREAM_ENGINE,
    DEFAULT_CHANNEL,
    DEFAULT_IDLE_SLEEP_SNAPSHOT,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_STREAM_ENGINE,
    DOMAIN,
    MAX_CAMERA_CONNECTIONS,
    STREAM_ENGINE_OPTIONS,
    STREAM_STATE_IDLE,
    STREAM_STATE_OFFLINE,
    STREAM_STATE_PRIVACY,
    STREAM_STATE_STREAMING,
)
from .local_client import AqaraG100LocalClient

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


@dataclass(slots=True)
class AqaraG100State:
    """Snapshot of all local Gatekeeper and Aqara App G100 states."""

    online: bool
    rtsp_port_open: bool
    talkback_port_open: bool
    latency_ms: float | None
    privacy_mode: bool
    idle_transmission_guard: bool
    status_led_enabled: bool
    active_channel: str
    is_transmitting: bool
    active_connections: int
    max_connections: int
    blocked_connections: int
    bitrate_kbps: float
    bytes_received: int
    stream_status: str
    gatekeeper_running: bool
    gatekeeper_port: int
    aqara_app_connected: bool
    motion_detected: bool
    person_detected: bool
    sound_detected: bool
    last_motion_event: str | None
    firmware_version: str | None = None
    spotlight_enabled: bool = False
    pet_detected: bool = False
    package_detected: bool = False
    vehicle_detected: bool = False
    camera_active: bool | None = None
    wifi_rssi: int | None = None
    stream_engine: str = DEFAULT_STREAM_ENGINE
    resources: dict[str, str] = field(default_factory=dict)


class AqaraG100DataUpdateCoordinator(DataUpdateCoordinator[AqaraG100State]):
    """Coordinate local TCP probing, RTSP Gatekeeper, and Aqara App sensors."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AqaraG100LocalClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.config_entry = entry
        self.client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}"
        )
        self._stream_stop_callbacks: list[Callable[[], Awaitable[None]]] = []
        self._provider_refresh_callbacks: list[Callable[[], Awaitable[None]]] = []

        # Persistent local control flags
        self.privacy_mode: bool = False
        self.idle_transmission_guard: bool = bool(
            entry.options.get(
                CONF_IDLE_SLEEP_SNAPSHOT,
                entry.data.get(CONF_IDLE_SLEEP_SNAPSHOT, DEFAULT_IDLE_SLEEP_SNAPSHOT),
            )
        )
        self.status_led_enabled: bool = True
        self.spotlight_enabled: bool = False
        self._privacy_override_until: float = 0.0
        self._led_override_until: float = 0.0
        self._spotlight_override_until: float = 0.0
        self._resource_values: dict[str, str] = {}
        self._resource_override_until: dict[str, float] = {}
        self.active_channel: str = str(
            entry.options.get(
                CONF_DEFAULT_CHANNEL,
                entry.data.get(CONF_DEFAULT_CHANNEL, DEFAULT_CHANNEL),
            )
        )
        if self.active_channel not in CHANNEL_OPTIONS:
            self.active_channel = DEFAULT_CHANNEL
        self.stream_engine: str = str(
            entry.options.get(
                CONF_STREAM_ENGINE,
                entry.data.get(CONF_STREAM_ENGINE, DEFAULT_STREAM_ENGINE),
            )
        )
        if self.stream_engine not in STREAM_ENGINE_OPTIONS:
            self.stream_engine = DEFAULT_STREAM_ENGINE

        self.client.gatekeeper.set_on_state_change(self._handle_gatekeeper_push)

    def register_stream_stop_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> Callable[[], None]:
        """Register camera stream-worker stop hook for instant Privacy/Stop."""
        self._stream_stop_callbacks.append(callback)

        def _unsub() -> None:
            if callback in self._stream_stop_callbacks:
                self._stream_stop_callbacks.remove(callback)

        return _unsub

    def register_provider_refresh_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> Callable[[], None]:
        """Register camera capability/provider refresh hook for stream engine switches."""
        self._provider_refresh_callbacks.append(callback)

        def _unsub() -> None:
            if callback in self._provider_refresh_callbacks:
                self._provider_refresh_callbacks.remove(callback)

        return _unsub

    def _build_state(
        self,
        *,
        online: bool,
        rtsp_port_open: bool,
        talkback_port_open: bool,
        latency_ms: float | None,
        telemetry: Any,
        aqara_app_connected: bool,
        motion_detected: bool,
        person_detected: bool,
        pet_detected: bool,
        package_detected: bool,
        vehicle_detected: bool,
        sound_detected: bool,
        last_motion_event: str | None,
        firmware_version: str | None,
        camera_active: bool | None,
        wifi_rssi: int | None,
    ) -> AqaraG100State:
        """Assemble immutable AqaraG100State from local and Aqara App telemetry."""
        return AqaraG100State(
            online=online,
            rtsp_port_open=rtsp_port_open,
            talkback_port_open=talkback_port_open,
            latency_ms=latency_ms,
            privacy_mode=self.privacy_mode,
            idle_transmission_guard=self.idle_transmission_guard,
            status_led_enabled=self.status_led_enabled,
            active_channel=self.active_channel,
            is_transmitting=telemetry.is_transmitting,
            active_connections=telemetry.active_connections,
            max_connections=MAX_CAMERA_CONNECTIONS,
            blocked_connections=telemetry.blocked_connections,
            bitrate_kbps=telemetry.bitrate_kbps,
            bytes_received=telemetry.bytes_received,
            stream_status=self._compute_stream_status(
                online=online, is_transmitting=telemetry.is_transmitting
            ),
            gatekeeper_running=telemetry.running,
            gatekeeper_port=telemetry.listen_port,
            aqara_app_connected=aqara_app_connected,
            motion_detected=motion_detected,
            person_detected=person_detected,
            sound_detected=sound_detected,
            last_motion_event=last_motion_event,
            firmware_version=firmware_version,
            spotlight_enabled=self.spotlight_enabled,
            pet_detected=pet_detected,
            package_detected=package_detected,
            vehicle_detected=vehicle_detected,
            camera_active=camera_active,
            wifi_rssi=wifi_rssi,
            stream_engine=self.stream_engine,
            resources=dict(self._resource_values),
        )

    def _handle_gatekeeper_push(self) -> None:
        """Push gatekeeper state changes straight to HA entities immediately."""
        if self.data is None:
            return
        telemetry = self.client.gatekeeper.get_telemetry()
        updated = self._build_state(
            online=self.data.online,
            rtsp_port_open=self.data.rtsp_port_open,
            talkback_port_open=self.data.talkback_port_open,
            latency_ms=self.data.latency_ms,
            telemetry=telemetry,
            aqara_app_connected=self.data.aqara_app_connected,
            motion_detected=self.data.motion_detected,
            person_detected=self.data.person_detected,
            pet_detected=self.data.pet_detected,
            package_detected=self.data.package_detected,
            vehicle_detected=self.data.vehicle_detected,
            sound_detected=self.data.sound_detected,
            last_motion_event=self.data.last_motion_event,
            firmware_version=self.data.firmware_version,
            camera_active=self.data.camera_active,
            wifi_rssi=self.data.wifi_rssi,
        )
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, updated)

    def _compute_stream_status(self, *, online: bool, is_transmitting: bool) -> str:
        """Determine unified stream status string."""
        if self.privacy_mode:
            return STREAM_STATE_PRIVACY
        if not online:
            return STREAM_STATE_OFFLINE
        if is_transmitting:
            return STREAM_STATE_STREAMING
        return STREAM_STATE_IDLE

    async def async_initialize(self) -> None:
        """Load persisted privacy/channel state and start built-in gatekeeper."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self.privacy_mode = bool(stored.get("privacy_mode", self.privacy_mode))
            self.idle_transmission_guard = bool(
                stored.get("idle_transmission_guard", self.idle_transmission_guard)
            )
            self.status_led_enabled = bool(
                stored.get("status_led_enabled", self.status_led_enabled)
            )
            stored_channel = stored.get("active_channel")
            if isinstance(stored_channel, str) and stored_channel in CHANNEL_OPTIONS:
                self.active_channel = stored_channel
            stored_engine = stored.get("stream_engine")
            if isinstance(stored_engine, str) and stored_engine in STREAM_ENGINE_OPTIONS:
                self.stream_engine = stored_engine

        await self.client.async_start()
        await self.client.gatekeeper.async_set_privacy_mode(self.privacy_mode)

    async def async_shutdown(self) -> None:
        """Stop the built-in gatekeeper on unload."""
        await self._async_stop_ha_streams()
        await self.client.async_stop()

    async def _async_stop_ha_streams(self) -> None:
        """Stop Home Assistant Camera.stream workers so they don't reconnect."""
        for stop_cb in list(self._stream_stop_callbacks):
            try:
                await stop_cb()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Error stopping HA camera stream worker: %s", err)

    async def _async_refresh_camera_providers(self) -> None:
        """Notify camera entities to refresh WebRTC/HLS capabilities."""
        for refresh_cb in list(self._provider_refresh_callbacks):
            try:
                await refresh_cb()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Error refreshing camera stream providers: %s", err)

    async def _async_save_state(self) -> None:
        """Persist local runtime toggles across Home Assistant restarts."""
        await self._store.async_save(
            {
                "privacy_mode": self.privacy_mode,
                "idle_transmission_guard": self.idle_transmission_guard,
                "status_led_enabled": self.status_led_enabled,
                "active_channel": self.active_channel,
                "stream_engine": self.stream_engine,
            }
        )

    async def _async_update_data(self) -> AqaraG100State:
        """Poll local TCP ports, gatekeeper telemetry, and Aqara App sensors."""
        probe = await self.client.async_probe_camera()
        app_state = await self.client.async_poll_aqara_app()
        telemetry = self.client.gatekeeper.get_telemetry()
        online = bool(probe["online"])

        # Sync status LED, spotlight, and privacy sleep state if changed from Aqara mobile app,
        # honoring a 15s propagation window after an HA write so stale cloud reads never revert UI.
        now = time.monotonic()
        if app_state.get("status_led") is not None:
            app_led = bool(app_state["status_led"])
            if app_led == self.status_led_enabled:
                self._led_override_until = 0.0
            elif now >= self._led_override_until:
                self.status_led_enabled = app_led

        if app_state.get("spotlight") is not None:
            app_spot = bool(app_state["spotlight"])
            if app_spot == self.spotlight_enabled:
                self._spotlight_override_until = 0.0
            elif now >= self._spotlight_override_until:
                self.spotlight_enabled = app_spot

        if app_state.get("privacy_sleep") is not None:
            app_sleep = bool(app_state["privacy_sleep"])
            if app_sleep == self.privacy_mode:
                self._privacy_override_until = 0.0
            elif now >= self._privacy_override_until:
                self.privacy_mode = app_sleep
                if app_sleep:
                    await self._async_stop_ha_streams()
                await self.client.gatekeeper.async_set_privacy_mode(app_sleep)
                await self._async_save_state()
                telemetry = self.client.gatekeeper.get_telemetry()

        for rid, app_val in (app_state.get("resources") or {}).items():
            if app_val == self._resource_values.get(rid):
                self._resource_override_until[rid] = 0.0
            elif now >= self._resource_override_until.get(rid, 0.0):
                self._resource_values[rid] = str(app_val)

        return self._build_state(
            online=online,
            rtsp_port_open=bool(probe["rtsp_port_open"]),
            talkback_port_open=bool(probe["talkback_port_open"]),
            latency_ms=probe["latency_ms"],
            telemetry=telemetry,
            aqara_app_connected=bool(app_state.get("connected", False)),
            motion_detected=bool(app_state.get("motion_detected", False)),
            person_detected=bool(app_state.get("person_detected", False)),
            pet_detected=bool(app_state.get("pet_detected", False)),
            package_detected=bool(app_state.get("package_detected", False)),
            vehicle_detected=bool(app_state.get("vehicle_detected", False)),
            sound_detected=bool(app_state.get("sound_detected", False)),
            last_motion_event=app_state.get("last_motion_event"),
            firmware_version=probe.get("firmware_version"),
            camera_active=app_state.get("camera_active"),
            wifi_rssi=app_state.get("wifi_rssi"),
        )

    async def async_set_privacy_mode(self, enabled: bool) -> None:
        """Toggle Privacy Mode on HA Gatekeeper and Aqara App."""
        prev_privacy = self.privacy_mode
        self._privacy_override_until = time.monotonic() + 15.0
        self.privacy_mode = enabled
        if enabled:
            await self._async_stop_ha_streams()
        try:
            await self.client.async_set_privacy_mode(enabled)
        except Exception:
            if not enabled:
                self.privacy_mode = prev_privacy
                self._privacy_override_until = 0.0
                await self.client.gatekeeper.async_set_privacy_mode(prev_privacy)
                await self.async_request_refresh()
            raise
        await self._async_save_state()
        await self.async_request_refresh()

    async def async_set_camera_power(self, enabled: bool) -> None:
        """Turn the camera on (disable privacy & wake) or off (enable privacy)."""
        await self.async_set_privacy_mode(not enabled)

    async def async_set_idle_transmission_guard(self, enabled: bool) -> None:
        """Toggle background snapshot suppression to keep camera idle."""
        self.idle_transmission_guard = enabled
        await self._async_save_state()
        await self.async_request_refresh()

    async def async_set_status_led(self, enabled: bool) -> None:
        """Toggle G100 status LED via Aqara Home App API."""
        await self.client.async_set_status_led(enabled)
        self._led_override_until = time.monotonic() + 15.0
        self.status_led_enabled = enabled
        await self._async_save_state()
        await self.async_request_refresh()

    async def async_set_spotlight(self, enabled: bool) -> None:
        """Toggle G100 built-in LED spotlight/flash via Aqara Home App API."""
        await self.client.async_set_spotlight(enabled)
        self._spotlight_override_until = time.monotonic() + 15.0
        self.spotlight_enabled = enabled
        await self.async_request_refresh()

    async def async_set_resource(
        self, resource_id: str, value: str | int, label: str = ""
    ) -> None:
        """Write an arbitrary G100 config resource with a 15s cloud-sync grace window."""
        await self.client.async_set_resource(resource_id, value, label)
        self._resource_override_until[resource_id] = time.monotonic() + 15.0
        self._resource_values[resource_id] = str(value)
        await self.async_request_refresh()

    async def async_play_audio(self, audio_source: str) -> int:
        """Stream an audio file/URL to the G100 speaker locally over LAN."""
        return await self.client.async_play_audio(audio_source)

    async def async_switch_channel(self, channel: str) -> None:
        """Switch active RTSP stream quality channel (ch1/ch2/ch3)."""
        if channel not in CHANNEL_OPTIONS:
            raise ValueError(f"Unsupported channel '{channel}'")
        self.active_channel = channel
        await self._async_save_state()
        await self._async_stop_ha_streams()
        await self.client.async_stop_transmission()
        await self.async_request_refresh()

    async def async_set_stream_engine(self, engine: str) -> None:
        """Switch frontend stream engine (auto / hls / webrtc) and reset stream."""
        if engine not in STREAM_ENGINE_OPTIONS:
            raise ValueError(f"Unsupported stream engine '{engine}'")
        self.stream_engine = engine
        await self._async_save_state()
        await self._async_stop_ha_streams()
        await self.client.async_stop_transmission()
        await self._async_refresh_camera_providers()
        await self.async_request_refresh()

    async def async_stop_transmission(self) -> None:
        """Forcefully disconnect HA stream worker and gatekeeper upstream socket."""
        await self._async_stop_ha_streams()
        await self.client.async_stop_transmission()
        await self.async_request_refresh()

    async def async_run_diagnostic_scan(self) -> str:
        """Run full G100 diagnostic scanner and post a persistent notification."""
        report = await self.client.async_run_diagnostic_scan()
        _LOGGER.info("Aqara G100 diagnostic scan (%s):\n%s", self.client.camera_ip, report)
        try:
            from homeassistant.components import persistent_notification

            persistent_notification.async_create(
                self.hass,
                report,
                title=f"Scansione Diagnostica Aqara G100 ({self.client.camera_ip})",
                notification_id=f"{DOMAIN}_diag_{self.config_entry.entry_id}",
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not create persistent notification: %s", err)
        await self.async_request_refresh()
        return report
