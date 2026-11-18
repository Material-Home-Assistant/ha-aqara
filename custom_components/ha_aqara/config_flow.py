"""Config flow with auto-discovery and Aqara Home App account sync."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .aqara_app_client import (
    AqaraAppClient,
    AqaraAppError,
    AqaraAuthError,
    _looks_like_legacy_mac_id,
)
from .const import (
    AQARA_REGIONS,
    CHANNEL_OPTIONS,
    CONF_AQARA_DEVICE_ID,
    CONF_AQARA_EMAIL,
    CONF_AQARA_PASSWORD,
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_CAMERA_IP,
    CONF_DEFAULT_CHANNEL,
    CONF_IDLE_SLEEP_SNAPSHOT,
    CONF_RTSP_PASSWORD,
    CONF_RTSP_PORT,
    CONF_RTSP_USERNAME,
    CONF_SELECTED_HOST,
    DEFAULT_AQARA_REGION,
    DEFAULT_CHANNEL,
    DEFAULT_IDLE_SLEEP_SNAPSHOT,
    DEFAULT_RTSP_PORT,
    DOMAIN,
    MANUAL_IP_OPTION,
)
from .discovery import (
    DiscoveredCamera,
    async_discover_cameras,
    async_lookup_mac_for_ip,
    async_probe_aqara_g100_candidate,
    async_query_aqara_mdns,
    format_lumi_device_id,
)
from .local_client import AqaraG100LocalClient

try:
    from homeassistant.helpers.selector import (
        SelectSelector,
        SelectSelectorConfig,
        SelectSelectorMode,
        TextSelector,
        TextSelectorConfig,
        TextSelectorType,
    )

    _PASSWORD_FIELD: Any = TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    )
    _CHANNEL_FIELD: Any = SelectSelector(
        SelectSelectorConfig(
            options=list(CHANNEL_OPTIONS),
            mode=SelectSelectorMode.LIST,
            translation_key="default_channel",
        )
    )
except ImportError:  # pragma: no cover
    _PASSWORD_FIELD = str
    _CHANNEL_FIELD = vol.In(CHANNEL_OPTIONS)

try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # pragma: no cover - legacy Home Assistant fallback
    from homeassistant.components.dhcp import DhcpServiceInfo  # type: ignore[no-redef]
    from homeassistant.components.zeroconf import ZeroconfServiceInfo  # type: ignore[no-redef]

_LOGGER = logging.getLogger(__name__)


async def _validate_camera_connection(
    hass: Any, config_data: dict[str, Any]
) -> dict[str, str]:
    """Validate TCP reachability of the Aqara G100 RTSP port."""
    errors: dict[str, str] = {}
    session = async_get_clientsession(hass)
    client = AqaraG100LocalClient(
        session=session,
        camera_ip=config_data[CONF_CAMERA_IP],
        rtsp_username=config_data.get(CONF_RTSP_USERNAME, ""),
        rtsp_password=config_data.get(CONF_RTSP_PASSWORD, ""),
        rtsp_port=int(config_data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)),
    )

    rtsp_ok, _ = await client.async_probe_tcp_port(client.rtsp_port, timeout=2.5)
    if not rtsp_ok:
        errors["base"] = "cannot_connect_camera"
    return errors


async def _authenticate_aqara_account(
    hass: Any, camera_ip: str, account_data: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    """Log into Aqara Home (if credentials provided) and resolve device_id."""
    errors: dict[str, str] = {}
    enriched = dict(account_data)
    email = str(enriched.get(CONF_AQARA_EMAIL, "")).strip()
    password = str(enriched.get(CONF_AQARA_PASSWORD, ""))
    region = str(enriched.get(CONF_AQARA_REGION, DEFAULT_AQARA_REGION))
    device_id = str(enriched.get(CONF_AQARA_DEVICE_ID, "")).strip()
    if camera_ip and _looks_like_legacy_mac_id(device_id):
        mdns_info = await async_query_aqara_mdns(camera_ip, timeout=1.0)
        if mdns_info.get("device_id"):
            device_id = str(mdns_info["device_id"])
            enriched[CONF_AQARA_DEVICE_ID] = device_id

    if not email and not password:
        enriched[CONF_AQARA_TOKEN] = ""
        enriched[CONF_AQARA_USER_ID] = ""
        enriched[CONF_AQARA_DEVICE_ID] = device_id
        return errors, enriched

    if not email or not password:
        errors["base"] = "aqara_credentials_incomplete"
        return errors, enriched

    session = async_get_clientsession(hass)
    app_client = AqaraAppClient(
        session=session,
        email=email,
        password=password,
        region=region,
        device_id=device_id,
    )
    try:
        user_id, token = await app_client.async_login()
        resolved_id = device_id or await app_client.async_resolve_device_id(camera_ip) or ""
    except AqaraAuthError as err:
        _LOGGER.warning("Aqara login failed: %s", err)
        errors["base"] = "aqara_auth_failed"
        return errors, enriched
    except AqaraAppError as err:
        _LOGGER.warning("Aqara API error during setup: %s", err)
        errors["base"] = "aqara_connect_failed"
        return errors, enriched

    enriched[CONF_AQARA_EMAIL] = email
    enriched[CONF_AQARA_USER_ID] = user_id
    enriched[CONF_AQARA_TOKEN] = token
    enriched[CONF_AQARA_DEVICE_ID] = resolved_id
    return errors, enriched


def _camera_schema_dict(
    defaults: dict[str, Any], *, include_ip: bool = True
) -> dict[Any, Any]:
    """Build core Aqara G100 RTSP & Gatekeeper configuration fields."""
    fields: dict[Any, Any] = {}
    if include_ip:
        fields[
            vol.Required(
                CONF_CAMERA_IP, default=defaults.get(CONF_CAMERA_IP, "")
            )
        ] = str
    fields.update(
        {
            vol.Optional(
                CONF_RTSP_USERNAME, default=defaults.get(CONF_RTSP_USERNAME, "")
            ): str,
            vol.Optional(
                CONF_RTSP_PASSWORD, default=defaults.get(CONF_RTSP_PASSWORD, "")
            ): _PASSWORD_FIELD,
            vol.Required(
                CONF_RTSP_PORT,
                default=defaults.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_DEFAULT_CHANNEL,
                default=defaults.get(CONF_DEFAULT_CHANNEL, DEFAULT_CHANNEL),
            ): _CHANNEL_FIELD,
            vol.Required(
                CONF_IDLE_SLEEP_SNAPSHOT,
                default=defaults.get(
                    CONF_IDLE_SLEEP_SNAPSHOT, DEFAULT_IDLE_SLEEP_SNAPSHOT
                ),
            ): bool,
        }
    )
    return fields


def _aqara_account_schema_dict(defaults: dict[str, Any]) -> dict[Any, Any]:
    """Build simple Aqara Home account fields (Email, Password, Region, ID)."""
    region_options = {key: val["name"] for key, val in AQARA_REGIONS.items()}
    return {
        vol.Optional(
            CONF_AQARA_EMAIL,
            default=defaults.get(CONF_AQARA_EMAIL, ""),
        ): str,
        vol.Optional(
            CONF_AQARA_PASSWORD,
            default=defaults.get(CONF_AQARA_PASSWORD, ""),
        ): _PASSWORD_FIELD,
        vol.Required(
            CONF_AQARA_REGION,
            default=defaults.get(CONF_AQARA_REGION, DEFAULT_AQARA_REGION),
        ): vol.In(region_options),
        vol.Optional(
            CONF_AQARA_DEVICE_ID,
            default=defaults.get(CONF_AQARA_DEVICE_ID, ""),
        ): str,
    }


class AqaraG100ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow for Aqara Camera G100 with auto-discovery."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow state."""
        self._base_data: dict[str, Any] = {}
        self._discovered_cameras: dict[str, DiscoveredCamera] = {}
        self._discovered_ip: str = ""
        self._discovered_port: int = DEFAULT_RTSP_PORT
        self._discovered_device_id: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return AqaraG100OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Start setup by automatically scanning the LAN for Aqara G100 cameras."""
        discovered = await async_discover_cameras(self.hass)
        if discovered:
            self._discovered_cameras = {cam.ip: cam for cam in discovered}
            return await self.async_step_pick_device()
        return await self.async_step_manual()

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user choose a discovered camera or fall back to manual IP."""
        if user_input is not None:
            selected = user_input[CONF_SELECTED_HOST]
            if selected == MANUAL_IP_OPTION:
                self._discovered_ip = ""
                self._discovered_port = DEFAULT_RTSP_PORT
                return await self.async_step_manual()

            cam = self._discovered_cameras.get(selected)
            self._discovered_ip = selected
            self._discovered_port = cam.rtsp_port if cam else DEFAULT_RTSP_PORT
            self._discovered_device_id = cam.device_id if cam else ""
            return await self.async_step_manual()

        options: dict[str, str] = {
            ip: cam.label for ip, cam in self._discovered_cameras.items()
        }
        options[MANUAL_IP_OPTION] = "Inserisci IP manualmente..."

        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SELECTED_HOST,
                        default=next(iter(self._discovered_cameras)),
                    ): vol.In(options)
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure camera IP (pre-filled if discovered) and RTSP credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await _validate_camera_connection(self.hass, user_input)
            if not errors:
                ip = user_input[CONF_CAMERA_IP].strip()
                self._async_abort_entries_match({CONF_CAMERA_IP: ip})
                if not self.unique_id:
                    await self.async_set_unique_id(f"ha_aqara_{ip}")
                self._abort_if_unique_id_configured()
                self._base_data = {**user_input, CONF_CAMERA_IP: ip}
                return await self.async_step_aqara_account()

        defaults: dict[str, Any] = dict(user_input or {})
        if not defaults and self._discovered_ip:
            defaults[CONF_CAMERA_IP] = self._discovered_ip
            defaults[CONF_RTSP_PORT] = self._discovered_port

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(_camera_schema_dict(defaults, include_ip=True)),
            errors=errors,
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Handle passive DHCP discovery for Aqara/Lumi devices."""
        ip = str(discovery_info.ip)
        mac = format_mac(discovery_info.macaddress)

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_CAMERA_IP: ip})
        self._async_abort_entries_match({CONF_CAMERA_IP: ip})

        candidate = await async_probe_aqara_g100_candidate(ip)
        if candidate is None:
            return self.async_abort(reason="not_aqara_camera")

        self._discovered_ip = ip
        self._discovered_port = candidate.rtsp_port
        self._discovered_device_id = (
            candidate.device_id or format_lumi_device_id(mac)
        )
        self.context["title_placeholders"] = {"name": f"Aqara G100 ({ip})"}
        return await self.async_step_discovery_confirm()

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle passive mDNS/Zeroconf discovery (_aqara / _hap / _rtsp)."""
        ip = str(getattr(discovery_info, "ip_address", None) or discovery_info.host)
        self._async_abort_entries_match({CONF_CAMERA_IP: ip})

        candidate = await async_probe_aqara_g100_candidate(ip)
        if candidate is None:
            return self.async_abort(reason="not_aqara_camera")

        props = getattr(discovery_info, "properties", None) or {}
        mdns_id = str(props.get("id") or candidate.device_id or "").strip()

        await self.async_set_unique_id(mdns_id or f"ha_aqara_{ip}")
        self._abort_if_unique_id_configured()

        self._discovered_ip = ip
        self._discovered_port = candidate.rtsp_port
        self._discovered_device_id = mdns_id
        self.context["title_placeholders"] = {"name": f"Aqara G100 ({ip})"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm passively discovered camera and ask for RTSP credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            merged = {**user_input, CONF_CAMERA_IP: self._discovered_ip}
            errors = await _validate_camera_connection(self.hass, merged)
            if not errors:
                self._base_data = merged
                return await self.async_step_aqara_account()

        defaults = dict(user_input or {})
        defaults.setdefault(CONF_RTSP_PORT, self._discovered_port)
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"ip": self._discovered_ip},
            data_schema=vol.Schema(_camera_schema_dict(defaults, include_ip=False)),
            errors=errors,
        )

    async def async_step_aqara_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Aqara Home App account for App Sleep, LED and Motion sensors."""
        errors: dict[str, str] = {}
        camera_ip = str(self._base_data.get(CONF_CAMERA_IP, ""))

        if user_input is not None:
            errors, enriched_account = await _authenticate_aqara_account(
                self.hass, camera_ip, user_input
            )
            if not errors:
                merged_data = {**self._base_data, **enriched_account}
                title = f"Aqara G100 ({camera_ip})"
                return self.async_create_entry(title=title, data=merged_data)

        # Auto-populate real lumi3.<id> from local mDNS first, fallback to ARP MAC
        auto_did = self._discovered_device_id
        if (not auto_did or _looks_like_legacy_mac_id(auto_did)) and camera_ip:
            mdns_info = await async_query_aqara_mdns(camera_ip, timeout=1.0)
            if mdns_info.get("device_id"):
                auto_did = str(mdns_info["device_id"])
            elif not auto_did:
                mac = await async_lookup_mac_for_ip(camera_ip)
                if mac:
                    auto_did = format_lumi_device_id(mac)

        defaults = dict(user_input or {})
        if auto_did and (
            not defaults.get(CONF_AQARA_DEVICE_ID)
            or _looks_like_legacy_mac_id(str(defaults.get(CONF_AQARA_DEVICE_ID, "")))
        ):
            defaults[CONF_AQARA_DEVICE_ID] = auto_did

        return self.async_show_form(
            step_id="aqara_account",
            data_schema=vol.Schema(_aqara_account_schema_dict(defaults)),
            errors=errors,
        )


class AqaraG100OptionsFlowHandler(OptionsFlow):
    """Handle runtime options for Aqara Camera G100."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the integration options."""
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            test_config = {**current, **user_input}
            errors = await _validate_camera_connection(self.hass, test_config)
            if not errors:
                errors, enriched = await _authenticate_aqara_account(
                    self.hass, test_config[CONF_CAMERA_IP], user_input
                )
                if not errors:
                    return self.async_create_entry(title="", data=enriched)

        defaults = dict(user_input or current)
        if defaults.get(CONF_CAMERA_IP) and _looks_like_legacy_mac_id(
            str(defaults.get(CONF_AQARA_DEVICE_ID, ""))
        ):
            cam_ip = str(defaults[CONF_CAMERA_IP])
            mdns_info = await async_query_aqara_mdns(cam_ip, timeout=1.0)
            if mdns_info.get("device_id"):
                defaults[CONF_AQARA_DEVICE_ID] = str(mdns_info["device_id"])
            elif not defaults.get(CONF_AQARA_DEVICE_ID):
                mac = await async_lookup_mac_for_ip(cam_ip)
                if mac:
                    defaults[CONF_AQARA_DEVICE_ID] = format_lumi_device_id(mac)

        combined_schema = {
            **_camera_schema_dict(defaults, include_ip=True),
            **_aqara_account_schema_dict(defaults),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(combined_schema),
            errors=errors,
        )
