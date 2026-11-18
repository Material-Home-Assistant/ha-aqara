"""Button platform for Aqara Camera G100 local stream control."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AqaraG100DataUpdateCoordinator
from .entity import AqaraG100Entity
from .local_client import AqaraG100Error


@dataclass(frozen=True, kw_only=True)
class AqaraG100ButtonDescription(ButtonEntityDescription):
    """Describe an Aqara G100 action button."""

    press_fn: Callable[[AqaraG100DataUpdateCoordinator], Awaitable[Any]]


BUTTON_DESCRIPTIONS: tuple[AqaraG100ButtonDescription, ...] = (
    AqaraG100ButtonDescription(
        key="force_stop_stream",
        translation_key="force_stop_stream",
        icon="mdi:stop-circle-outline",
        press_fn=lambda coord: coord.async_stop_transmission(),
    ),
    AqaraG100ButtonDescription(
        key="refresh_status",
        translation_key="refresh_status",
        icon="mdi:refresh",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coord: coord.async_request_refresh(),
    ),
    AqaraG100ButtonDescription(
        key="run_diagnostic_scan",
        translation_key="run_diagnostic_scan",
        icon="mdi:radar",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coord: coord.async_run_diagnostic_scan(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aqara G100 button entities."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AqaraG100Button(coordinator, description)
        for description in BUTTON_DESCRIPTIONS
    )


class AqaraG100Button(AqaraG100Entity, ButtonEntity):
    """Representation of an Aqara G100 action button."""

    entity_description: AqaraG100ButtonDescription

    def __init__(
        self,
        coordinator: AqaraG100DataUpdateCoordinator,
        description: AqaraG100ButtonDescription,
    ) -> None:
        """Initialize the button entity."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Execute the local stream action."""
        try:
            await self.entity_description.press_fn(self.coordinator)
        except AqaraG100Error as err:
            raise HomeAssistantError(str(err)) from err
