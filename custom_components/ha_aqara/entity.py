"""Base entity for Aqara Camera G100 integration."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL_ID, MODEL_NAME
from .coordinator import AqaraG100DataUpdateCoordinator


def async_remove_stale_entities(
    hass: HomeAssistant,
    entry_id: str,
    platform_domain: str,
    stale_suffixes: Iterable[str],
) -> None:
    """Remove obsolete entities from registry when optional Aqara sync is disabled."""
    registry = er.async_get(hass)
    for suffix in stale_suffixes:
        unique_id = f"{entry_id}_{suffix}"
        entity_id = registry.async_get_entity_id(platform_domain, DOMAIN, unique_id)
        if entity_id:
            registry.async_remove(entity_id)


class AqaraG100Entity(CoordinatorEntity[AqaraG100DataUpdateCoordinator]):
    """Shared base entity grouping all platforms under one G100 device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AqaraG100DataUpdateCoordinator,
        unique_suffix: str,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Aqara G100 ({coordinator.client.camera_ip})",
            manufacturer=MANUFACTURER,
            model=f"{MODEL_NAME} ({MODEL_ID})",
            sw_version=coordinator.client.firmware_version,
        )
