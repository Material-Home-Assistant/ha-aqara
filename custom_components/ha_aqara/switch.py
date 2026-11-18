"""Switch platform for Aqara Camera G100."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AqaraG100DataUpdateCoordinator, AqaraG100State
from .entity import AqaraG100Entity, async_remove_stale_entities
from .local_client import AqaraG100Error


@dataclass(frozen=True, kw_only=True)
class AqaraG100SwitchDescription(SwitchEntityDescription):
    """Describe an Aqara G100 switch entity."""

    value_fn: Callable[[AqaraG100State], bool]
    set_fn: Callable[[AqaraG100DataUpdateCoordinator, bool], Awaitable[None]]
    always_available: bool = False
    requires_aqara_app: bool = False


_CONFIG_SWITCH_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("14.70.85", "motion_detection_enabled", "mdi:motion-sensor", "Rilevamento Movimento"),
    ("14.77.85", "person_detection_enabled", "mdi:account-search", "Rilevamento Persone"),
    ("14.78.85", "animal_detection_enabled", "mdi:paw", "Rilevamento Animali"),
    ("14.75.85", "face_detection_enabled", "mdi:face-recognition", "Rilevamento Volti"),
    ("14.159.85", "vehicle_detection_enabled", "mdi:car-info", "Rilevamento Veicoli"),
    ("4.94.85", "package_detection_enabled", "mdi:package-variant-closed", "Rilevamento Pacchi"),
    ("4.193.85", "fire_detection_enabled", "mdi:fire-alert", "Rilevamento Fuoco"),
    ("4.63.85", "disable_audio_recording", "mdi:microphone-off", "Disattiva Audio"),
    ("14.4.85", "timestamp_overlay", "mdi:clock-digital", "Sovrapposizione Ora"),
    ("4.60.85", "logo_watermark", "mdi:watermark", "Logo Aqara"),
    ("4.76.85", "detection_box", "mdi:vector-square", "Riquadro Rilevamento"),
    ("4.187.85", "timed_snapshot_10m", "mdi:camera-timer", "Istantanea 10 Min"),
    ("4.89.85", "offline_notification", "mdi:bell-alert", "Notifica Offline"),
    ("4.27.85", "timelapse_video", "mdi:timelapse", "Video Timelapse"),
    ("4.99.85", "anti_theft_lock", "mdi:lock-alert", "Blocco Antifurto"),
)


def _make_resource_switch_desc(
    rid: str, key: str, icon: str, label: str
) -> AqaraG100SwitchDescription:
    """Create a generic table-driven switch description for an Aqara RID."""
    return AqaraG100SwitchDescription(
        key=key,
        translation_key=key,
        icon=icon,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.resources.get(rid) == "1",
        set_fn=lambda coord, val: coord.async_set_resource(rid, "1" if val else "0", label),
        always_available=True,
        requires_aqara_app=True,
    )


SWITCH_DESCRIPTIONS: tuple[AqaraG100SwitchDescription, ...] = (
    AqaraG100SwitchDescription(
        key="camera_power",
        translation_key="camera_power",
        icon="mdi:camera",
        value_fn=lambda state: not state.privacy_mode,
        set_fn=lambda coord, val: coord.async_set_camera_power(val),
        always_available=True,
    ),
    AqaraG100SwitchDescription(
        key="privacy_mode",
        translation_key="privacy_mode",
        icon="mdi:eye-off",
        value_fn=lambda state: state.privacy_mode,
        set_fn=lambda coord, val: coord.async_set_privacy_mode(val),
        always_available=True,
    ),
    AqaraG100SwitchDescription(
        key="idle_transmission_guard",
        translation_key="idle_transmission_guard",
        icon="mdi:sleep",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.idle_transmission_guard,
        set_fn=lambda coord, val: coord.async_set_idle_transmission_guard(val),
        always_available=True,
    ),
    AqaraG100SwitchDescription(
        key="status_led",
        translation_key="status_led",
        icon="mdi:led-on",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.status_led_enabled,
        set_fn=lambda coord, val: coord.async_set_status_led(val),
        always_available=True,
        requires_aqara_app=True,
    ),
    AqaraG100SwitchDescription(
        key="spotlight",
        translation_key="spotlight",
        icon="mdi:flashlight",
        value_fn=lambda state: state.spotlight_enabled,
        set_fn=lambda coord, val: coord.async_set_spotlight(val),
        always_available=True,
        requires_aqara_app=True,
    ),
    *(
        _make_resource_switch_desc(rid, key, icon, label)
        for rid, key, icon, label in _CONFIG_SWITCH_SPECS
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Aqara G100 switch entities."""
    coordinator: AqaraG100DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    has_app = coordinator.client.has_aqara_app_sync
    if not has_app:
        async_remove_stale_entities(
            hass,
            entry.entry_id,
            "switch",
            (d.key for d in SWITCH_DESCRIPTIONS if d.requires_aqara_app),
        )
    async_add_entities(
        AqaraG100Switch(coordinator, description)
        for description in SWITCH_DESCRIPTIONS
        if has_app or not description.requires_aqara_app
    )


class AqaraG100Switch(AqaraG100Entity, SwitchEntity):
    """Representation of an Aqara G100 control switch."""

    entity_description: AqaraG100SwitchDescription

    def __init__(
        self,
        coordinator: AqaraG100DataUpdateCoordinator,
        description: AqaraG100SwitchDescription,
    ) -> None:
        """Initialize switch entity."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Keep switches controllable even while offline/in privacy."""
        if self.entity_description.always_available:
            return True
        return super().available

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is currently on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def icon(self) -> str | None:
        """Provide state-aware icon for power, privacy mode, LED, and spotlight."""
        if self.entity_description.key == "camera_power":
            return "mdi:camera" if self.is_on else "mdi:camera-off"
        if self.entity_description.key == "privacy_mode":
            return "mdi:eye-off" if self.is_on else "mdi:eye"
        if self.entity_description.key == "status_led":
            return "mdi:led-on" if self.is_on else "mdi:led-off"
        if self.entity_description.key == "spotlight":
            return "mdi:flashlight" if self.is_on else "mdi:flashlight-off"
        return super().icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose Aqara App sync status on control switches."""
        if self.entity_description.key in (
            "camera_power",
            "privacy_mode",
            "status_led",
            "spotlight",
        ):
            return {
                "aqara_app_sync_configured": (
                    self.coordinator.client.has_aqara_app_sync
                ),
                "aqara_device_id": self.coordinator.client.aqara_app.device_id,
            }
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await self.entity_description.set_fn(self.coordinator, True)
        except AqaraG100Error as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await self.entity_description.set_fn(self.coordinator, False)
        except AqaraG100Error as err:
            raise HomeAssistantError(str(err)) from err
