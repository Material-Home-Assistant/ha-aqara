"""Binary sensor platform for Aqara Camera G100."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AqaraG100DataUpdateCoordinator, AqaraG100State
from .entity import AqaraG100Entity, async_remove_stale_entities


@dataclass(frozen=True, kw_only=True)
class AqaraG100BinarySensorDescription(BinarySensorEntityDescription):
    """Describe an Aqara G100 binary sensor."""

    value_fn: Callable[[AqaraG100State], bool | None]
    requires_aqara_app: bool = False


BINARY_SENSOR_DESCRIPTIONS: tuple[AqaraG100BinarySensorDescription, ...] = (
    AqaraG100BinarySensorDescription(
        key="motion_detected",
        translation_key="motion_detected",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda state: state.motion_detected,
        requires_aqara_app=True,
    ),
    AqaraG100BinarySensorDescription(
        key="person_detected",
        translation_key="person_detected",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        icon="mdi:account-alert",
        value_fn=lambda state: state.person_detected,
        requires_aqara_app=True,
    ),
    AqaraG100BinarySensorDescription(
        key="pet_detected",
        translation_key="pet_detected",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        icon="mdi:paw",
        value_fn=lambda state: state.pet_detected,
        requires_aqara_app=True,
    ),
    AqaraG100BinarySensorDescription(
        key="package_detected",
        translation_key="package_detected",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        icon="mdi:package-variant-closed",
        value_fn=lambda state: state.package_detected,
        requires_aqara_app=True,
    ),
    AqaraG100BinarySensorDescription(
        key="vehicle_detected",
        translation_key="vehicle_detected",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        icon="mdi:car",
        value_fn=lambda state: state.vehicle_detected,
        requires_aqara_app=True,
    ),
    AqaraG100BinarySensorDescription(
        key="sound_detected",
        translation_key="sound_detected",
        device_class=BinarySensorDeviceClass.SOUND,
        value_fn=lambda state: state.sound_detected,
        requires_aqara_app=True,
    ),
    AqaraG100BinarySensorDescription(
        key="camera_active",
        translation_key="camera_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:camera-iris",
        value_fn=lambda state: state.camera_active,
        requires_aqara_app=True,
    ),
    AqaraG100BinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.online,
    ),
    AqaraG100BinarySensorDescription(
        key="is_transmitting",
        translation_key="is_transmitting",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:video-wireless",
        value_fn=lambda state: state.is_transmitting,
    ),
    AqaraG100BinarySensorDescription(
        key="rtsp_port_open",
        translation_key="rtsp_port_open",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:lan-connect",
        value_fn=lambda state: state.rtsp_port_open,
    ),
    AqaraG100BinarySensorDescription(
        key="talkback_port_open",
        translation_key="talkback_port_open",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:microphone",
        value_fn=lambda state: state.talkback_port_open,
    ),
    AqaraG100BinarySensorDescription(
        key="gatekeeper_running",
        translation_key="gatekeeper_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:shield-lock",
        value_fn=lambda state: state.gatekeeper_running,
    ),
    AqaraG100BinarySensorDescription(
        key="aqara_app_connected",
        translation_key="aqara_app_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud-sync",
        value_fn=lambda state: state.aqara_app_connected,
        requires_aqara_app=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aqara G100 binary sensor entities."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    has_app = coordinator.client.has_aqara_app_sync
    if not has_app:
        async_remove_stale_entities(
            hass,
            entry.entry_id,
            "binary_sensor",
            (d.key for d in BINARY_SENSOR_DESCRIPTIONS if d.requires_aqara_app),
        )
    async_add_entities(
        AqaraG100BinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
        if has_app or not description.requires_aqara_app
    )


class AqaraG100BinarySensor(AqaraG100Entity, BinarySensorEntity):
    """Representation of an Aqara G100 motion/activity/diagnostic binary sensor."""

    entity_description: AqaraG100BinarySensorDescription

    def __init__(
        self,
        coordinator: AqaraG100DataUpdateCoordinator,
        description: AqaraG100BinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor condition holds."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
