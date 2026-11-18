"""Aqara Camera G100 (100% Local + Aqara App Sync) integration for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ATTR_AUDIO_SOURCE,
    ATTR_CHANNEL,
    ATTR_ENABLED,
    ATTR_ENTITY_ID,
    CHANNEL_OPTIONS,
    CONF_AQARA_DEVICE_ID,
    CONF_AQARA_EMAIL,
    CONF_AQARA_PASSWORD,
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_CAMERA_IP,
    CONF_RTSP_PASSWORD,
    CONF_RTSP_PORT,
    CONF_RTSP_USERNAME,
    DEFAULT_AQARA_REGION,
    DEFAULT_RTSP_PORT,
    DOMAIN,
    PLATFORMS,
    SERVICE_PLAY_AUDIO,
    SERVICE_RUN_DIAGNOSTIC_SCAN,
    SERVICE_SET_PRIVACY_MODE,
    SERVICE_STOP_TRANSMISSION,
    SERVICE_SWITCH_CHANNEL,
)
from .coordinator import AqaraG100DataUpdateCoordinator
from .local_client import AqaraG100Error, AqaraG100LocalClient

_LOGGER = logging.getLogger(__name__)


def _resolve_target_coordinators(
    hass: HomeAssistant, call: ServiceCall
) -> list[AqaraG100DataUpdateCoordinator]:
    """Resolve target coordinators from optional entity_id(s) or all entries."""
    coordinators: dict[str, AqaraG100DataUpdateCoordinator] = hass.data.get(
        DOMAIN, {}
    )
    if not coordinators:
        return []

    raw_entities = call.data.get(ATTR_ENTITY_ID)
    if not raw_entities:
        return list(coordinators.values())

    entity_ids = (
        [raw_entities] if isinstance(raw_entities, str) else list(raw_entities)
    )
    registry = er.async_get(hass)
    resolved: dict[str, AqaraG100DataUpdateCoordinator] = {}
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry and entry.config_entry_id in coordinators:
            resolved[entry.config_entry_id] = coordinators[entry.config_entry_id]

    return list(resolved.values())


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once across all config entries."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_PRIVACY_MODE):
        return

    async def handle_set_privacy_mode(call: ServiceCall) -> None:
        enabled: bool = call.data[ATTR_ENABLED]
        targets = _resolve_target_coordinators(hass, call)
        if not targets:
            raise HomeAssistantError("Nessuna telecamera Aqara G100 trovata")
        for coordinator in targets:
            try:
                await coordinator.async_set_privacy_mode(enabled)
            except AqaraG100Error as err:
                raise HomeAssistantError(str(err)) from err

    async def handle_stop_transmission(call: ServiceCall) -> None:
        targets = _resolve_target_coordinators(hass, call)
        if not targets:
            raise HomeAssistantError("Nessuna telecamera Aqara G100 trovata")
        for coordinator in targets:
            try:
                await coordinator.async_stop_transmission()
            except AqaraG100Error as err:
                raise HomeAssistantError(str(err)) from err

    async def handle_switch_channel(call: ServiceCall) -> None:
        channel: str = call.data[ATTR_CHANNEL]
        targets = _resolve_target_coordinators(hass, call)
        if not targets:
            raise HomeAssistantError("Nessuna telecamera Aqara G100 trovata")
        for coordinator in targets:
            try:
                await coordinator.async_switch_channel(channel)
            except (AqaraG100Error, ValueError) as err:
                raise HomeAssistantError(str(err)) from err

    async def handle_run_diagnostic_scan(call: ServiceCall) -> None:
        targets = _resolve_target_coordinators(hass, call)
        if not targets:
            raise HomeAssistantError("Nessuna telecamera Aqara G100 trovata")
        for coordinator in targets:
            try:
                await coordinator.async_run_diagnostic_scan()
            except AqaraG100Error as err:
                raise HomeAssistantError(str(err)) from err

    async def handle_play_audio(call: ServiceCall) -> None:
        audio_source: str = call.data[ATTR_AUDIO_SOURCE]
        targets = _resolve_target_coordinators(hass, call)
        if not targets:
            raise HomeAssistantError("Nessuna telecamera Aqara G100 trovata")
        for coordinator in targets:
            try:
                await coordinator.async_play_audio(audio_source)
            except AqaraG100Error as err:
                raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PRIVACY_MODE,
        handle_set_privacy_mode,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
                vol.Required(ATTR_ENABLED): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_TRANSMISSION,
        handle_stop_transmission,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SWITCH_CHANNEL,
        handle_switch_channel,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
                vol.Required(ATTR_CHANNEL): vol.In(list(CHANNEL_OPTIONS)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_DIAGNOSTIC_SCAN,
        handle_run_diagnostic_scan,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY_AUDIO,
        handle_play_audio,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
                vol.Required(ATTR_AUDIO_SOURCE): cv.string,
            }
        ),
    )


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Remove domain services when the last config entry is unloaded."""
    for service in (
        SERVICE_SET_PRIVACY_MODE,
        SERVICE_STOP_TRANSMISSION,
        SERVICE_SWITCH_CHANNEL,
        SERVICE_RUN_DIAGNOSTIC_SCAN,
        SERVICE_PLAY_AUDIO,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aqara Camera G100 from a config entry."""
    cfg: dict[str, Any] = {**entry.data, **entry.options}
    session = async_get_clientsession(hass)

    client = AqaraG100LocalClient(
        session=session,
        camera_ip=cfg[CONF_CAMERA_IP],
        rtsp_username=cfg.get(CONF_RTSP_USERNAME, ""),
        rtsp_password=cfg.get(CONF_RTSP_PASSWORD, ""),
        rtsp_port=int(cfg.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)),
        aqara_email=cfg.get(CONF_AQARA_EMAIL, ""),
        aqara_password=cfg.get(CONF_AQARA_PASSWORD, ""),
        aqara_region=cfg.get(CONF_AQARA_REGION, DEFAULT_AQARA_REGION),
        aqara_device_id=cfg.get(CONF_AQARA_DEVICE_ID, ""),
        aqara_token=cfg.get(CONF_AQARA_TOKEN, ""),
        aqara_user_id=cfg.get(CONF_AQARA_USER_ID, ""),
    )

    coordinator = AqaraG100DataUpdateCoordinator(hass, entry, client)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Aqara Camera G100 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data: dict[str, AqaraG100DataUpdateCoordinator] = hass.data.get(
            DOMAIN, {}
        )
        coordinator = domain_data.pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_shutdown()
        if not domain_data:
            _async_unregister_services(hass)
    return unload_ok
