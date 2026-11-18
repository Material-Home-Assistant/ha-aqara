"""Diagnostics support for Aqara Camera G100."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AQARA_EMAIL,
    CONF_AQARA_PASSWORD,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_RTSP_PASSWORD,
    CONF_RTSP_USERNAME,
    DOMAIN,
)
from .coordinator import AqaraG100DataUpdateCoordinator

_TO_REDACT = {
    CONF_RTSP_USERNAME,
    CONF_RTSP_PASSWORD,
    CONF_AQARA_EMAIL,
    CONF_AQARA_PASSWORD,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry with sensitive credentials redacted."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    state_dict = asdict(coordinator.data) if coordinator.data is not None else {}
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "data": async_redact_data(entry.data, _TO_REDACT),
            "options": async_redact_data(entry.options, _TO_REDACT),
        },
        "client": {
            "camera_ip": coordinator.client.camera_ip,
            "rtsp_port": coordinator.client.rtsp_port,
            "firmware_version": coordinator.client.firmware_version,
            "has_aqara_app_sync": coordinator.client.has_aqara_app_sync,
            "aqara_device_id": coordinator.client.aqara_app.device_id,
            "aqara_region": coordinator.client.aqara_app.region,
        },
        "state": state_dict,
    }
