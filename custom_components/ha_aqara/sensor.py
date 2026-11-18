"""Sensor platform for Aqara Camera G100."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    DOMAIN,
    MAX_CAMERA_CONNECTIONS,
    STREAM_STATE_IDLE,
    STREAM_STATE_OFFLINE,
    STREAM_STATE_PRIVACY,
    STREAM_STATE_STREAMING,
)
from .coordinator import AqaraG100DataUpdateCoordinator, AqaraG100State
from .entity import AqaraG100Entity, async_remove_stale_entities


@dataclass(frozen=True, kw_only=True)
class AqaraG100SensorDescription(SensorEntityDescription):
    """Describe an Aqara G100 sensor entity."""

    value_fn: Callable[[AqaraG100State], StateType]
    requires_aqara_app: bool = False


SENSOR_DESCRIPTIONS: tuple[AqaraG100SensorDescription, ...] = (
    AqaraG100SensorDescription(
        key="stream_status",
        translation_key="stream_status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            STREAM_STATE_STREAMING,
            STREAM_STATE_IDLE,
            STREAM_STATE_PRIVACY,
            STREAM_STATE_OFFLINE,
        ],
        icon="mdi:cctv",
        value_fn=lambda state: state.stream_status,
    ),
    AqaraG100SensorDescription(
        key="last_motion_event",
        translation_key="last_motion_event",
        icon="mdi:motion-sensor",
        value_fn=lambda state: state.last_motion_event,
        requires_aqara_app=True,
    ),
    AqaraG100SensorDescription(
        key="active_connections",
        translation_key="active_connections",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:connection",
        value_fn=lambda state: state.active_connections,
    ),
    AqaraG100SensorDescription(
        key="bitrate_kbps",
        translation_key="bitrate_kbps",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBITS_PER_SECOND,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:speedometer",
        value_fn=lambda state: state.bitrate_kbps,
    ),
    AqaraG100SensorDescription(
        key="latency_ms",
        translation_key="latency_ms",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-outline",
        value_fn=lambda state: state.latency_ms,
    ),
    AqaraG100SensorDescription(
        key="bytes_received",
        translation_key="bytes_received",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:download-network",
        value_fn=lambda state: state.bytes_received,
    ),
    AqaraG100SensorDescription(
        key="blocked_connections",
        translation_key="blocked_connections",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:shield-remove",
        value_fn=lambda state: state.blocked_connections,
    ),
    AqaraG100SensorDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi",
        value_fn=lambda state: state.wifi_rssi,
        requires_aqara_app=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aqara G100 sensor entities."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    has_app = coordinator.client.has_aqara_app_sync
    if not has_app:
        async_remove_stale_entities(
            hass,
            entry.entry_id,
            "sensor",
            (d.key for d in SENSOR_DESCRIPTIONS if d.requires_aqara_app),
        )
    async_add_entities(
        AqaraG100Sensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
        if has_app or not description.requires_aqara_app
    )


class AqaraG100Sensor(AqaraG100Entity, SensorEntity):
    """Representation of an Aqara G100 stream/motion sensor."""

    entity_description: AqaraG100SensorDescription

    def __init__(
        self,
        coordinator: AqaraG100DataUpdateCoordinator,
        description: AqaraG100SensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the current sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose gatekeeper slot limits on active_connections sensor."""
        if self.entity_description.key == "active_connections":
            return {
                "max_allowed_connections": MAX_CAMERA_CONNECTIONS,
                "gatekeeper_port": (
                    self.coordinator.data.gatekeeper_port
                    if self.coordinator.data
                    else 0
                ),
            }
        return None
