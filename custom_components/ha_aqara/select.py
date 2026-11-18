"""Select platform for Aqara Camera G100 stream resolution channel."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHANNEL_OPTIONS,
    DEVICE_LANGUAGE_OPTIONS,
    DOMAIN,
    NIGHT_VISION_OPTIONS,
    POWER_FREQUENCY_OPTIONS,
    SENSITIVITY_OPTIONS,
    STREAM_ENGINE_OPTIONS,
    TIMESTAMP_FORMAT_OPTIONS,
)
from .coordinator import AqaraG100DataUpdateCoordinator
from .entity import AqaraG100Entity, async_remove_stale_entities
from .local_client import AqaraG100Error


@dataclass(frozen=True, kw_only=True)
class AqaraG100ResourceSelectSpec:
    """Describe a resource-backed G100 Select entity."""

    rid: str
    key: str
    icon: str
    label: str
    wire_to_slug: dict[str, str]


_RESOURCE_SELECT_SPECS: tuple[AqaraG100ResourceSelectSpec, ...] = (
    AqaraG100ResourceSelectSpec(
        rid="14.73.85",
        key="night_vision",
        icon="mdi:weather-night",
        label="Visione Notturna",
        wire_to_slug=NIGHT_VISION_OPTIONS,
    ),
    AqaraG100ResourceSelectSpec(
        rid="14.104.85",
        key="motion_sensitivity",
        icon="mdi:tune",
        label="Sensibilità Movimento",
        wire_to_slug=SENSITIVITY_OPTIONS,
    ),
    AqaraG100ResourceSelectSpec(
        rid="14.136.85",
        key="person_sensitivity",
        icon="mdi:account-cog",
        label="Sensibilità Persone",
        wire_to_slug=SENSITIVITY_OPTIONS,
    ),
    AqaraG100ResourceSelectSpec(
        rid="14.120.85",
        key="power_frequency",
        icon="mdi:sine-wave",
        label="Frequenza Rete",
        wire_to_slug=POWER_FREQUENCY_OPTIONS,
    ),
    AqaraG100ResourceSelectSpec(
        rid="14.134.85",
        key="timestamp_format",
        icon="mdi:calendar-clock",
        label="Formato Data/Ora",
        wire_to_slug=TIMESTAMP_FORMAT_OPTIONS,
    ),
    AqaraG100ResourceSelectSpec(
        rid="14.10.85",
        key="device_language",
        icon="mdi:translate",
        label="Lingua Dispositivo",
        wire_to_slug=DEVICE_LANGUAGE_OPTIONS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aqara G100 select entities."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = [
        AqaraG100ChannelSelect(coordinator),
        AqaraG100EngineSelect(coordinator),
    ]
    if coordinator.client.has_aqara_app_sync:
        entities.extend(
            AqaraG100ResourceSelect(coordinator, spec)
            for spec in _RESOURCE_SELECT_SPECS
        )
    else:
        async_remove_stale_entities(
            hass,
            entry.entry_id,
            "select",
            (spec.key for spec in _RESOURCE_SELECT_SPECS),
        )
    async_add_entities(entities)


class AqaraG100ChannelSelect(AqaraG100Entity, SelectEntity):
    """Select entity to switch gatekeeper RTSP channel between ch1, ch2 and ch3."""

    _attr_translation_key = "stream_channel"
    _attr_icon = "mdi:video-switch"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(CHANNEL_OPTIONS)

    def __init__(self, coordinator: AqaraG100DataUpdateCoordinator) -> None:
        """Initialize the channel selector."""
        super().__init__(coordinator, "stream_channel")

    @property
    def current_option(self) -> str | None:
        """Return currently selected RTSP channel."""
        if self.coordinator.data is not None:
            return self.coordinator.data.active_channel
        return self.coordinator.active_channel

    async def async_select_option(self, option: str) -> None:
        """Switch RTSP channel dynamically on the built-in gatekeeper."""
        try:
            await self.coordinator.async_switch_channel(option)
        except (AqaraG100Error, ValueError) as err:
            raise HomeAssistantError(str(err)) from err


class AqaraG100EngineSelect(AqaraG100Entity, SelectEntity):
    """Select entity to choose between Auto, HLS (TCP), and HA built-in WebRTC."""

    _attr_translation_key = "stream_engine"
    _attr_icon = "mdi:video-input-antenna"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(STREAM_ENGINE_OPTIONS)

    def __init__(self, coordinator: AqaraG100DataUpdateCoordinator) -> None:
        """Initialize the stream engine selector."""
        super().__init__(coordinator, "stream_engine")

    @property
    def current_option(self) -> str | None:
        """Return currently selected stream engine."""
        if self.coordinator.data is not None:
            return self.coordinator.data.stream_engine
        return self.coordinator.stream_engine

    async def async_select_option(self, option: str) -> None:
        """Switch frontend stream engine dynamically and clear old stream slots."""
        try:
            await self.coordinator.async_set_stream_engine(option)
        except (AqaraG100Error, ValueError) as err:
            raise HomeAssistantError(str(err)) from err


class AqaraG100ResourceSelect(AqaraG100Entity, SelectEntity):
    """Generic Select entity backed by an Aqara G100 config resource."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: AqaraG100DataUpdateCoordinator,
        spec: AqaraG100ResourceSelectSpec,
    ) -> None:
        """Initialize the resource selector."""
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._slug_to_wire = {slug: wire for wire, slug in spec.wire_to_slug.items()}
        self._attr_translation_key = spec.key
        self._attr_icon = spec.icon
        self._attr_options = list(spec.wire_to_slug.values())

    @property
    def available(self) -> bool:
        """Remain available whenever entity is present so user sees explicit errors."""
        return True

    @property
    def current_option(self) -> str | None:
        """Return currently active translated option slug."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.resources.get(self._spec.rid)
        if raw is None:
            return None
        return self._spec.wire_to_slug.get(str(raw))

    async def async_select_option(self, option: str) -> None:
        """Send selected option wire value to the G100 via Aqara App API."""
        wire = self._slug_to_wire.get(option)
        if wire is None:
            raise HomeAssistantError(f"Opzione non valida per {self._spec.label}: {option}")
        try:
            await self.coordinator.async_set_resource(
                self._spec.rid, wire, self._spec.label
            )
        except AqaraG100Error as err:
            raise HomeAssistantError(str(err)) from err
