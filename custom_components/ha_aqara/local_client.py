"""Local async client + Aqara Home App sync controller for Aqara G100."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import time
from typing import Any

import aiohttp

from .aqara_app_client import (
    AqaraAppClient,
    AqaraAppError,
    _looks_like_legacy_mac_id,
)
from .const import (
    DEFAULT_AQARA_REGION,
    DEFAULT_RTSP_PORT,
    DEFAULT_TALKBACK_TCP_PORT,
    MAX_CAMERA_CONNECTIONS,
)
from .discovery import async_query_aqara_mdns
from .rtsp_gatekeeper import AqaraG100SingleConnGatekeeper
from .talkback import async_play_audio_to_camera

_LOGGER = logging.getLogger(__name__)


class AqaraG100Error(Exception):
    """Base exception for Aqara G100 client errors."""


class AqaraG100ConnectionError(AqaraG100Error):
    """Raised when the camera cannot be reached locally."""


class AqaraG100LocalClient:
    """Unified local Gatekeeper + mDNS + Aqara Home App controller for G100."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        camera_ip: str,
        rtsp_username: str,
        rtsp_password: str,
        rtsp_port: int = DEFAULT_RTSP_PORT,
        aqara_email: str = "",
        aqara_password: str = "",
        aqara_region: str = DEFAULT_AQARA_REGION,
        aqara_device_id: str = "",
        aqara_token: str = "",
        aqara_user_id: str = "",
    ) -> None:
        """Initialize local Gatekeeper and Aqara Home App sync client."""
        self._session = session
        self.camera_ip = camera_ip.strip()
        self.rtsp_username = rtsp_username
        self.rtsp_password = rtsp_password
        self.rtsp_port = rtsp_port
        self.firmware_version: str | None = None
        self.mdns_txt: dict[str, str] = {}
        self._last_mdns_probe: float = 0.0

        self.gatekeeper = AqaraG100SingleConnGatekeeper(
            camera_ip=self.camera_ip,
            rtsp_port=self.rtsp_port,
            rtsp_username=self.rtsp_username,
            rtsp_password=self.rtsp_password,
            max_connections=MAX_CAMERA_CONNECTIONS,
        )
        self.aqara_app = AqaraAppClient(
            session=session,
            email=aqara_email,
            password=aqara_password,
            region=aqara_region,
            device_id=aqara_device_id,
            token=aqara_token,
            user_id=aqara_user_id,
        )

    @property
    def has_aqara_app_sync(self) -> bool:
        """Return True if Aqara Home App credentials/session are configured."""
        return self.aqara_app.is_configured

    async def async_refresh_mdns_metadata(self, *, force: bool = False) -> None:
        """Query local UDP 5353 mDNS on the camera to keep ID & firmware fresh."""
        now = time.monotonic()
        if not force and (now - self._last_mdns_probe) < 60.0:
            return
        self._last_mdns_probe = now
        info = await async_query_aqara_mdns(self.camera_ip, timeout=0.9)
        if not info:
            return
        if info.get("firmware"):
            self.firmware_version = str(info["firmware"])
        if isinstance(info.get("txt"), dict):
            self.mdns_txt = dict(info["txt"])
        mdns_did = str(info.get("device_id") or "").strip()
        if mdns_did and (
            not self.aqara_app.device_id
            or _looks_like_legacy_mac_id(self.aqara_app.device_id)
        ):
            if self.aqara_app.device_id != mdns_did:
                _LOGGER.info(
                    "Risolto ID reale G100 via mDNS per %s: %s",
                    self.camera_ip,
                    mdns_did,
                )
            self.aqara_app.device_id = mdns_did

    async def async_start(self) -> int:
        """Start the built-in single-connection RTSP gatekeeper and resolve ID."""
        port = await self.gatekeeper.async_start()
        await self.async_refresh_mdns_metadata(force=True)
        if self.aqara_app.is_configured and (
            not self.aqara_app.device_id
            or _looks_like_legacy_mac_id(self.aqara_app.device_id)
        ):
            with suppress(AqaraAppError):
                await self.aqara_app.async_resolve_device_id(self.camera_ip)
        return port

    async def async_stop(self) -> None:
        """Shut down the built-in RTSP gatekeeper and active session."""
        await self.gatekeeper.async_stop()

    async def async_probe_tcp_port(
        self, port: int, timeout: float = 1.5
    ) -> tuple[bool, float | None]:
        """Check TCP reachability without sending RTSP PLAY/DESCRIBE commands."""
        start = time.monotonic()
        writer: asyncio.StreamWriter | None = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.camera_ip, port),
                timeout=timeout,
            )
        except (OSError, TimeoutError):
            return False, None
        else:
            latency_ms = round((time.monotonic() - start) * 1000.0, 1)
            return True, latency_ms
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

    async def async_probe_camera(self) -> dict[str, Any]:
        """Probe camera RTSP (8554), audio P2P (54324), and local mDNS."""
        (rtsp_open, rtsp_latency), (talkback_open, talkback_latency), _ = (
            await asyncio.gather(
                self.async_probe_tcp_port(self.rtsp_port),
                self.async_probe_tcp_port(DEFAULT_TALKBACK_TCP_PORT),
                self.async_refresh_mdns_metadata(force=False),
            )
        )
        latency = rtsp_latency if rtsp_latency is not None else talkback_latency
        return {
            "online": rtsp_open or talkback_open,
            "rtsp_port_open": rtsp_open,
            "talkback_port_open": talkback_open,
            "latency_ms": latency,
            "firmware_version": self.firmware_version,
            "mdns_txt": self.mdns_txt,
        }

    async def async_stop_transmission(self) -> None:
        """Immediately disconnect the active RTSP session on the gatekeeper."""
        await self.gatekeeper.async_force_disconnect()

    async def _async_ensure_aqara_device_id(self) -> None:
        """Resolve real Aqara DID via mDNS/Cloud if missing or legacy MAC format."""
        if self.aqara_app.is_configured and (
            not self.aqara_app.device_id
            or _looks_like_legacy_mac_id(self.aqara_app.device_id)
        ):
            with suppress(AqaraAppError):
                await self.aqara_app.async_resolve_device_id(self.camera_ip)

    @staticmethod
    def _empty_aqara_poll() -> dict[str, Any]:
        """Return baseline unconfigured/error poll snapshot."""
        return {
            "connected": False,
            "privacy_sleep": None,
            "status_led": None,
            "spotlight": None,
            "camera_active": None,
            "wifi_rssi": None,
            "motion_detected": False,
            "person_detected": False,
            "pet_detected": False,
            "package_detected": False,
            "vehicle_detected": False,
            "sound_detected": False,
            "last_motion_event": None,
            "resources": {},
        }

    async def async_set_privacy_mode(self, enabled: bool) -> None:
        """Enable or disable Privacy Mode both on HA Gatekeeper and Aqara App."""
        await self.gatekeeper.async_set_privacy_mode(enabled)
        if self.aqara_app.is_configured:
            await self._async_ensure_aqara_device_id()
            try:
                await self.aqara_app.async_set_camera_sleep(enabled)
            except AqaraAppError as err:
                _LOGGER.warning("Errore sincronizzazione Privacy su App Aqara: %s", err)
                raise AqaraG100Error(str(err)) from err

    async def async_set_status_led(self, enabled: bool) -> None:
        """Toggle G100 status LED via Aqara Home App API."""
        await self._async_ensure_aqara_device_id()
        try:
            await self.aqara_app.async_set_status_led(enabled)
        except AqaraAppError as err:
            raise AqaraG100Error(str(err)) from err

    async def async_set_spotlight(self, enabled: bool) -> None:
        """Toggle G100 built-in LED spotlight/flash via Aqara Home App API."""
        await self._async_ensure_aqara_device_id()
        try:
            await self.aqara_app.async_set_spotlight(enabled)
        except AqaraAppError as err:
            raise AqaraG100Error(str(err)) from err

    async def async_set_resource(
        self, resource_id: str, value: str | int, label: str = ""
    ) -> None:
        """Write an arbitrary G100 config resource via Aqara Home App API."""
        await self._async_ensure_aqara_device_id()
        try:
            await self.aqara_app.async_set_resource(resource_id, value, label)
        except AqaraAppError as err:
            raise AqaraG100Error(str(err)) from err

    async def async_play_audio(self, audio_source: str) -> int:
        """Play audio file/URL to the G100 speaker locally over LAN (TCP 54324 / UDP 54323)."""
        try:
            return await async_play_audio_to_camera(self.camera_ip, audio_source)
        except (OSError, TimeoutError, RuntimeError) as err:
            raise AqaraG100Error(f"Errore invio audio locale alla G100: {err}") from err

    async def async_poll_aqara_app(self) -> dict[str, Any]:
        """Poll sleep, status LED, spotlight, config resources, and AI/motion sensors."""
        if not self.aqara_app.is_configured:
            return self._empty_aqara_poll()
        await self._async_ensure_aqara_device_id()
        try:
            return await self.aqara_app.async_poll_device_resources()
        except AqaraAppError as err:
            _LOGGER.debug("Aqara App poll error for %s: %s", self.camera_ip, err)
            return self._empty_aqara_poll()

    async def async_run_diagnostic_scan(self) -> str:
        """Execute full mDNS + TCP ports + Aqara App RID/Trait scan."""
        try:
            return await self.aqara_app.async_run_full_diagnostic(self.camera_ip)
        except AqaraAppError as err:
            raise AqaraG100Error(str(err)) from err
