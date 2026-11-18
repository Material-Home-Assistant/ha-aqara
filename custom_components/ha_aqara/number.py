"""Number platform for Aqara Camera G100 configuration values."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AqaraG100DataUpdateCoordinator
from .entity import AqaraG100Entity, async_remove_stale_entities
from .local_client import AqaraG100Error


@dataclass(frozen=True, kw_only=True)
class AqaraG100NumberSpec:
    """Describe a resource-backed numeric setting on Aqara G100."""

    rid: str
    key: str
    icon: str
    label: str
    min_value: float
    max_value: float
    step: float
    unit: str | None = None


_NUMBER_SPECS: tuple[AqaraG100NumberSpec, ...] = (
    AqaraG100NumberSpec(
        rid="14.11.85",
        key="device_volume",
        icon="mdi:volume-high",
        label="Volume Altoparlante",
        min_value=0,
        max_value=100,
        step=1,
        unit=PERCENTAGE,
    ),
    AqaraG100NumberSpec(
        rid="14.105.85",
        key="motion_interval",
        icon="mdi:timer-outline",
        label="Intervallo Rilevamento Movimento",
        min_value=15,
        max_value=600,
        step=1,
        unit=UnitOfTime.SECONDS,
    ),
    AqaraG100NumberSpec(
        rid="14.114.85",
        key="person_interval",
        icon="mdi:account-clock",
        label="Intervallo Rilevamento Persone",
        min_value=15,
        max_value=600,
        step=1,
        unit=UnitOfTime.SECONDS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aqara G100 number entities."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.client.has_aqara_app_sync:
        async_remove_stale_entities(
            hass,
            entry.entry_id,
            "number",
            (spec.key for spec in _NUMBER_SPECS),
        )
        return
    async_add_entities(
        AqaraG100ResourceNumber(coordinator, spec) for spec in _NUMBER_SPECS
    )


class AqaraG100ResourceNumber(AqaraG100Entity, NumberEntity):
    """Generic Number entity backed by an Aqara G100 config resource."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: AqaraG100DataUpdateCoordinator,
        spec: AqaraG100NumberSpec,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._attr_translation_key = spec.key
        self._attr_icon = spec.icon
        self._attr_native_min_value = spec.min_value
        self._attr_native_max_value = spec.max_value
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit

    @property
    def available(self) -> bool:
        """Keep available so user receives clear errors if Aqara App sync is missing."""
        return True

    @property
    def native_value(self) -> float | None:
        """Return current numeric value from the G100."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.resources.get(self._spec.rid)
        if raw is None:
            return None
        with suppress(TypeError, ValueError):
            return float(int(float(raw)))
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Update numeric config value on the G100 via Aqara App API."""
        try:
            await self.coordinator.async_set_resource(
                self._spec.rid, str(int(round(value))), self._spec.label
            )
        except AqaraG100Error as err:
            raise HomeAssistantError(str(err)) from err
