"""Aqara Home App API client (pure Python RSA + MD5, zero external deps)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import logging
import secrets
import time
from typing import Any
import uuid

import aiohttp

dt_util = None
with suppress(ImportError):
    from homeassistant.util import dt as dt_util

from .const import (
    AQARA_REGIONS,
    AQARA_SIGNING_FALLBACKS,
    DEFAULT_AQARA_REGION,
    G100_DIAGNOSTIC_RIDS,
    G100_DIAGNOSTIC_TRAITS,
    G100_NAMED_DIAGNOSTIC_OPTIONS,
    G100_POLL_CONFIG_RIDS,
    G100_POLL_TRAITS,
    MOTION_HOLD_SECONDS,
    RID_CAMERA_SLEEP_LIST,
    RID_LED_NIGHT_TIP,
    RID_MOTION_LIST,
    RID_PERSON_LIST,
    RID_PRIVACY_SET_VIDEO,
    RID_PRIVACY_VIDEO_SWITCH_LIST,
    RID_SOUND_LIST,
    RID_SPOTLIGHT,
    RID_STATUS_LED_LIST,
    RID_STATUS_LED_PACKED_PERIOD,
    TRAIT_G100_ACTIVE,
    TRAIT_G100_OCCUPANCY,
    TRAIT_G100_PACKAGE,
    TRAIT_G100_PERSON,
    TRAIT_G100_PET,
    TRAIT_G100_VEHICLE,
    TRAIT_G100_WIFI_RSSI,
)
from .discovery import (
    async_lookup_mac_for_ip,
    async_query_aqara_mdns,
    async_scan_tcp_ports,
    format_lumi_device_id,
)

_LOGGER = logging.getLogger(__name__)

_AQARA_PUBKEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCG46slB57013JJs4Vvj5cVyMpR
9b+B2F+YJU6qhBEYbiEmIdWpFPpOuBikDs2FcPS19MiWq1IrmxJtkICGurqImRUt
4lP688IWlEmqHfSxSRf2+aH0cH8VWZ2OaZn5DWSIHIPBF2kxM71q8stmoYiV0oZs
rZzBHsMuBwA4LQdxBwIDAQAB
-----END PUBLIC KEY-----"""

_AUTH_FAIL_CODES = frozenset({108, 109, 401, 802, 804, 1001, 1002})


class AqaraAppError(Exception):
    """Raised when an Aqara App API call fails."""


class AqaraAuthError(AqaraAppError):
    """Raised when Aqara account authentication fails."""


def _read_asn1_tlv(buf: bytes, offset: int) -> tuple[int, bytes, int]:
    """Read a single ASN.1 DER Tag-Length-Value element."""
    tag = buf[offset]
    length_byte = buf[offset + 1]
    pos = offset + 2
    if length_byte < 0x80:
        length = length_byte
    else:
        num_len_bytes = length_byte & 0x7F
        length = int.from_bytes(buf[pos : pos + num_len_bytes], "big")
        pos += num_len_bytes
    value = buf[pos : pos + length]
    return tag, value, pos + length


def _parse_rsa_public_key_pem(pem: str) -> tuple[int, int]:
    """Extract (modulus_n, exponent_e) from an X.509 SubjectPublicKeyInfo PEM."""
    b64_lines = [
        line.strip()
        for line in pem.strip().splitlines()
        if line.strip() and not line.strip().startswith("-----")
    ]
    der = base64.b64decode("".join(b64_lines))
    outer_tag, outer_seq, _ = _read_asn1_tlv(der, 0)
    if outer_tag != 0x30:
        raise ValueError("Invalid SPKI SEQUENCE")

    _, _, next_off = _read_asn1_tlv(outer_seq, 0)
    bit_tag, bit_val, _ = _read_asn1_tlv(outer_seq, next_off)
    if bit_tag != 0x03 or not bit_val:
        raise ValueError("Invalid SPKI BIT STRING")

    rsa_seq_der = bit_val[1:]
    seq_tag, rsa_seq, _ = _read_asn1_tlv(rsa_seq_der, 0)
    if seq_tag != 0x30:
        raise ValueError("Invalid RSA public key SEQUENCE")

    n_tag, n_bytes, e_off = _read_asn1_tlv(rsa_seq, 0)
    e_tag, e_bytes, _ = _read_asn1_tlv(rsa_seq, e_off)
    if n_tag != 0x02 or e_tag != 0x02:
        raise ValueError("Invalid RSA INTEGER components")

    return int.from_bytes(n_bytes, "big"), int.from_bytes(e_bytes, "big")


_RSA_N, _RSA_E = _parse_rsa_public_key_pem(_AQARA_PUBKEY_PEM)
_RSA_KEY_BYTES = (_RSA_N.bit_length() + 7) // 8


def encrypt_aqara_password(password: str) -> str:
    """Encrypt plain password with MD5 + Aqara RSA PKCS#1 v1.5 public key."""
    md5_hex = hashlib.md5(password.encode("utf-8")).hexdigest().encode("ascii")
    ps_len = _RSA_KEY_BYTES - 3 - len(md5_hex)
    if ps_len < 8:
        raise ValueError("RSA key too small for PKCS#1 v1.5 padding")
    padding = bytes(secrets.randbelow(255) + 1 for _ in range(ps_len))
    em = b"\x00\x02" + padding + b"\x00" + md5_hex
    cipher_int = pow(int.from_bytes(em, "big"), _RSA_E, _RSA_N)
    return base64.b64encode(cipher_int.to_bytes(_RSA_KEY_BYTES, "big")).decode("ascii")


def _format_local_timestamp(ts_sec: float) -> str:
    """Format epoch seconds as 'YYYY-MM-DD HH:MM:SS' in local Home Assistant timezone."""
    utc_dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    if dt_util is not None:
        local_dt = dt_util.as_local(utc_dt)
    else:
        local_dt = utc_dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")


def _extract_resource_entries(result: Any) -> list[dict[str, Any]]:
    """Normalize Aqara res/query result structures into a flat list of dicts."""
    entries: list[dict[str, Any]] = []
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                if isinstance(item.get("resources"), list):
                    entries.extend(
                        r for r in item["resources"] if isinstance(r, dict)
                    )
                else:
                    entries.append(item)
    elif isinstance(result, dict):
        if isinstance(result.get("resources"), list):
            entries.extend(r for r in result["resources"] if isinstance(r, dict))
        elif isinstance(result.get("data"), list):
            return _extract_resource_entries(result["data"])
    return entries


def _extract_named_values(result: Any) -> dict[str, Any]:
    """Extract key -> value pairs from /app/v1.0/lumi/res/query or view/data/query."""
    found: dict[str, Any] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for elem in node:
                _walk(elem)
            return
        if not isinstance(node, dict):
            return

        rid = node.get("resourceId") or node.get("resId") or node.get("resName")
        if rid and "value" in node and node["value"] is not None:
            found[str(rid)] = node["value"]

        for key, val in node.items():
            if key in (
                "resourceId",
                "resId",
                "resName",
                "subjectId",
                "did",
                "viewId",
                "options",
                "timeStamp",
                "timestamp",
                "value",
            ):
                continue
            if isinstance(val, dict) and "value" in val and val["value"] is not None:
                found[str(key)] = val["value"]
            elif isinstance(val, (list, dict)):
                _walk(val)
            elif val is not None and ("." in str(key) or key in ("set_video", "device_night_tip_light", "time_sleep_enable")):
                found[str(key)] = val

    _walk(result)
    return found


def _extract_trait_value(traits: list[dict[str, Any]], path: str) -> Any:
    """Return raw value for a given V3 trait path, or None if missing."""
    for tr in traits:
        if str(tr.get("path") or "") == path:
            return tr.get("value")
    return None


def _is_truthy_value(val: Any) -> bool:
    """Interpret Aqara resource values ('1', 1, True, 'true')."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return int(val) != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "on", "yes")
    return False


def decode_packed_led_period(raw_value: Any) -> tuple[bool, int, int]:
    """Decode Aqara 14.21.85 packed period integer.

    Wire math: ``wire = (start_min << 12) | (end_min << 1) | disabled_bit0``
    ``disabled_bit0 == 0`` means the "Turn off indicator light" period is ACTIVE.
    Returns ``(turn_off_period_enabled, start_min, end_min)``.
    """
    wire = int(str(raw_value).strip())
    start_min = (wire >> 12) & 0x7FF
    end_min = (wire >> 1) & 0x7FF
    turn_off_enabled = (wire & 1) == 0
    return turn_off_enabled, start_min, end_min


def encode_packed_led_period(
    *, turn_off_enabled: bool, start_min: int = 0, end_min: int = 1439
) -> str:
    """Encode Aqara 14.21.85 packed period integer as decimal string."""
    s_min = max(0, min(1439, int(start_min)))
    e_min = max(0, min(1439, int(end_min)))
    wire = (s_min << 12) | (e_min << 1) | (0 if turn_off_enabled else 1)
    return str(wire)


def is_led_currently_enabled_from_period(raw_value: Any) -> bool:
    """Return True if the physical status LED is ON given 14.21.85 period value."""
    turn_off_enabled, start_min, end_min = decode_packed_led_period(raw_value)
    if not turn_off_enabled:
        return True
    # Aqara encodes "all day 24h" as either 00:00..23:59 or start == end (e.g. 21:00..21:00)
    if start_min == end_min or (start_min == 0 and end_min >= 1439):
        return False
    now_local = datetime.now()
    now_min = now_local.hour * 60 + now_local.minute
    if start_min <= end_min:
        in_off_window = start_min <= now_min <= end_min
    else:
        in_off_window = now_min >= start_min or now_min <= end_min
    return not in_off_window


def _looks_like_legacy_mac_id(device_id: str) -> bool:
    """Return True if device_id is empty, a raw MAC, or a lumi1.<mac> guess."""
    clean = device_id.strip().lower()
    if not clean:
        return True
    if ":" in clean or clean.startswith("lumi."):
        return True
    return clean.startswith("lumi1.")


class AqaraAppClient:
    """Client for Aqara Home app endpoints (Sleep/Privacy, Status LED, Motion)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str = "",
        password: str = "",
        region: str = DEFAULT_AQARA_REGION,
        device_id: str = "",
        token: str = "",
        user_id: str = "",
    ) -> None:
        """Initialize the Aqara App API client."""
        self._session = session
        self.email = email.strip()
        self.password = password
        self.region = region if region in AQARA_REGIONS else DEFAULT_AQARA_REGION
        self.device_id = device_id.strip()
        self.token = token.strip()
        self.user_id = user_id.strip()
        self._phone_id = str(uuid.uuid4()).upper()

        region_cfg = AQARA_REGIONS[self.region]
        self._appid: str = region_cfg["appid"]
        self._appkey: str = region_cfg["appkey"]
        self._signing_probed: bool = False

        # Cached working RIDs, period range, and request shapes
        self._sleep_rid: str | None = None
        self._led_rid: str | None = None
        self._led_period_range: tuple[int, int] | None = None
        self._query_uses_resource_ids: bool = False
        self._write_mode: str | None = None

        # Edge-triggered event timestamp tracking (motion, person, sound, pet, package, vehicle)
        self._event_ts: dict[str, int] = {}
        self._event_until: dict[str, float] = {}
        self._last_motion_ts_ms: int = 0
        self._last_motion_iso: str | None = None

    @property
    def is_configured(self) -> bool:
        """Return True if Aqara credentials or session token are present."""
        return bool((self.email and self.password) or (self.token and self.user_id))

    @property
    def server(self) -> str:
        """Return regional Aqara RPC base URL."""
        return AQARA_REGIONS[self.region]["server"]

    @property
    def appid(self) -> str:
        """Return active Aqara AppId."""
        return self._appid

    @property
    def appkey(self) -> str:
        """Return active Aqara AppKey."""
        return self._appkey

    def _build_signed_headers(
        self, sign_source: str, *, include_token: bool = True
    ) -> dict[str, str]:
        """Build Aqara App signed HTTP headers."""
        nonce = hashlib.md5(str(uuid.uuid4()).encode("utf-8")).hexdigest()
        ts_ms = str(round(time.time() * 1000))

        if include_token and self.token:
            sign_raw = (
                f"Appid={self._appid}&Nonce={nonce}&Time={ts_ms}"
                f"&Token={self.token}&{sign_source}&{self._appkey}"
            )
        else:
            sign_raw = (
                f"Appid={self._appid}&Nonce={nonce}&Time={ts_ms}"
                f"&{sign_source}&{self._appkey}"
            )

        headers = {
            "User-Agent": "okhttp/4.12.0",
            "App-Version": "6.1.6",
            "Sys-Type": "1",
            "Lang": "en",
            "Phone-Model": "Home Assistant##Server",
            "PhoneId": self._phone_id,
            "Content-Type": "application/json",
            "Area": self.region,
            "Appid": self._appid,
            "Nonce": nonce,
            "Time": ts_ms,
            "Sign": hashlib.md5(sign_raw.encode("utf-8")).hexdigest(),
        }
        if include_token and self.token:
            headers["Token"] = self.token
        if include_token and self.user_id:
            headers["Userid"] = self.user_id
        return headers

    async def _raw_request(
        self,
        method: str,
        url: str,
        sign_source: str,
        *,
        data: str | None = None,
        include_token: bool = True,
    ) -> dict[str, Any]:
        """Execute a signed HTTP request and parse JSON response."""
        headers = self._build_signed_headers(sign_source, include_token=include_token)
        try:
            async with self._session.request(
                method,
                url,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                parsed = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise AqaraAppError(f"Aqara API error ({url}): {err}") from err

        if not isinstance(parsed, dict):
            raise AqaraAppError(f"Unexpected Aqara response format for {url}")
        return parsed

    async def _async_signed_request(
        self,
        method: str,
        path: str,
        *,
        sign_source: str,
        url_query: str = "",
        body: str | None = None,
        include_token: bool = True,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        """Send a signed request with code-106 signing fallback and token refresh."""
        if include_token and not self.token and self.email and self.password:
            await self.async_login()

        url = f"{self.server}{path}{url_query}"
        data = await self._raw_request(
            method, url, sign_source, data=body, include_token=include_token
        )

        if str(data.get("code")) == "106" and not self._signing_probed:
            self._signing_probed = True
            seed = (self._appid, self._appkey)
            for appid, appkey in AQARA_SIGNING_FALLBACKS:
                if (appid, appkey) == seed:
                    continue
                self._appid, self._appkey = appid, appkey
                retry = await self._raw_request(
                    method, url, sign_source, data=body, include_token=include_token
                )
                if str(retry.get("code")) != "106":
                    _LOGGER.info(
                        "Aqara region %s switched signing credential to appid %s..",
                        self.region,
                        appid[:10],
                    )
                    data = retry
                    break
            else:
                self._appid, self._appkey = seed

        code = data.get("code")
        msg = str(data.get("message") or data.get("msg") or "")
        code_int: int | None = None
        with suppress(TypeError, ValueError):
            code_int = int(code) if code is not None else None

        if (
            include_token
            and retry_on_auth
            and self.email
            and self.password
            and (
                (code_int is not None and code_int in _AUTH_FAIL_CODES)
                or "token" in msg.lower()
            )
        ):
            _LOGGER.debug("Aqara token expired (code=%s), re-logging in", code)
            await self.async_login()
            return await self._async_signed_request(
                method,
                path,
                sign_source=sign_source,
                url_query=url_query,
                body=body,
                include_token=include_token,
                retry_on_auth=False,
            )

        return data

    async def _async_post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        include_token: bool = True,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        """Send a signed POST request (using canonical json.dumps formatting)."""
        body_str = json.dumps(payload)
        return await self._async_signed_request(
            "POST",
            path,
            sign_source=body_str,
            body=body_str,
            include_token=include_token,
            retry_on_auth=retry_on_auth,
        )

    async def _async_get(
        self,
        path: str,
        sorted_query: str = "",
        *,
        include_token: bool = True,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        """Send a signed GET request (sign_source is sorted bare query string)."""
        url_query = f"?{sorted_query}" if sorted_query else ""
        return await self._async_signed_request(
            "GET",
            path,
            sign_source=sorted_query,
            url_query=url_query,
            body=None,
            include_token=include_token,
            retry_on_auth=retry_on_auth,
        )

    async def async_login(self) -> tuple[str, str]:
        """Authenticate with Aqara Home credentials and store user_id + token."""
        if not self.email or not self.password:
            raise AqaraAuthError("Credenziali account Aqara mancanti.")

        payload = {
            "account": self.email,
            "encryptType": 2,
            "password": encrypt_aqara_password(self.password),
        }
        data = await self._async_post(
            "/app/v1.0/lumi/user/login",
            payload,
            include_token=False,
            retry_on_auth=False,
        )
        if data.get("code") != 0:
            raise AqaraAuthError(
                f"Login Aqara fallito (code={data.get('code')}): "
                f"{data.get('message') or data.get('msg') or 'credenziali errate'}"
            )

        result = data.get("result") or {}
        self.user_id = str(result.get("userId") or "")
        self.token = str(result.get("token") or "")
        if not self.token or not self.user_id:
            raise AqaraAuthError("Login Aqara riuscito ma token/userId assenti.")

        return self.user_id, self.token

    async def async_list_account_devices(self) -> list[dict[str, Any]]:
        """Fetch full device inventory across all Aqara homes."""
        if not self.is_configured:
            return []

        devices: list[dict[str, Any]] = []
        with suppress(AqaraAppError):
            resp = await self._async_get(
                "/app/v1.0/lumi/app/position/device/query",
                "size=300&startIndex=0",
            )
            if resp.get("code") == 0:
                res = resp.get("result") or {}
                if isinstance(res, dict) and isinstance(res.get("devices"), list):
                    devices.extend(d for d in res["devices"] if isinstance(d, dict))

        if devices:
            return devices

        for path, body in (
            ("/app/v1.0/lumi/dev/query", {}),
            ("/app/v1.0/lumi/position/dev/query", {"positionId": ""}),
        ):
            with suppress(AqaraAppError):
                resp = await self._async_post(path, body)
                if resp.get("code") == 0:
                    res = resp.get("result")
                    if isinstance(res, list):
                        devices.extend(d for d in res if isinstance(d, dict))
                    elif isinstance(res, dict):
                        for key in ("data", "devices", "devList"):
                            if isinstance(res.get(key), list):
                                devices.extend(
                                    d for d in res[key] if isinstance(d, dict)
                                )
                    if devices:
                        break
        return devices

    async def async_resolve_device_id(self, camera_ip: str) -> str | None:
        """Auto-detect authoritative Aqara DID via local mDNS first, then Cloud/ARP."""
        mdns_info = await async_query_aqara_mdns(camera_ip, timeout=1.2)
        mdns_did = str(mdns_info.get("device_id") or "").strip()
        if mdns_did:
            self.device_id = mdns_did
            return mdns_did

        arp_did: str | None = None
        mac = await async_lookup_mac_for_ip(camera_ip)
        if mac:
            arp_did = format_lumi_device_id(mac) or None

        if self.is_configured:
            candidates = await self.async_list_account_devices()
            if mac:
                clean_mac = mac.replace(":", "").lower()
                for dev in candidates:
                    did = str(dev.get("did") or dev.get("subjectId") or "")
                    dev_mac = str(dev.get("mac") or "").replace(":", "").lower()
                    if did and (clean_mac in did.lower() or clean_mac == dev_mac):
                        self.device_id = did
                        return did

            for dev in candidates:
                model = str(dev.get("model") or "").lower()
                name = str(dev.get("deviceName") or dev.get("name") or "").lower()
                if "agl005" in model or "g100" in name or "lumi.camera" in model:
                    did = str(dev.get("did") or dev.get("subjectId") or "")
                    if did:
                        self.device_id = did
                        return did

        if self.device_id and not _looks_like_legacy_mac_id(self.device_id):
            return self.device_id

        if arp_did and not self.device_id:
            self.device_id = arp_did
        return self.device_id or None

    async def _async_query_resources_batch(
        self, resource_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Query up to 16 Aqara resource values with automatic endpoint/shape fallback."""
        attempts: list[tuple[str, str, dict[str, Any]]] = [
            (
                "res_by_rid",
                "/app/v1.0/lumi/res/query/by/resourceId",
                {"data": [{"options": resource_ids, "subjectId": self.device_id}]},
            ),
            (
                "options",
                "/app/v1.0/lumi/res/query",
                {"data": [{"subjectId": self.device_id, "options": resource_ids}]},
            ),
            (
                "resourceIds",
                "/app/v1.0/lumi/res/query",
                {"data": [{"subjectId": self.device_id, "resourceIds": resource_ids}]},
            ),
        ]
        if self._query_uses_resource_ids:
            attempts.insert(0, attempts.pop())

        for shape_name, path, payload in attempts:
            with suppress(AqaraAppError):
                resp = await self._async_post(path, payload)
                if resp.get("code") == 0:
                    entries = _extract_resource_entries(resp.get("result"))
                    if entries or shape_name == attempts[-1][0]:
                        self._query_uses_resource_ids = shape_name == "resourceIds"
                        return entries
        return []

    async def _async_query_resources(
        self, resource_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Query Aqara device resource values cleanly in batches of 16."""
        if not self.device_id or not resource_ids:
            return []
        unique_ids = list(dict.fromkeys(resource_ids))
        batches = [unique_ids[i : i + 16] for i in range(0, len(unique_ids), 16)]
        if len(batches) == 1:
            return await self._async_query_resources_batch(batches[0])
        results = await asyncio.gather(
            *(self._async_query_resources_batch(batch) for batch in batches)
        )
        merged: list[dict[str, Any]] = []
        for chunk in results:
            merged.extend(chunk)
        return merged

    async def async_query_traits(self, paths: list[str]) -> list[dict[str, Any]]:
        """Read and subscribe to V3 G100 traits via /app/v1.0/lumi/app/qlink/trait/read."""
        if not self.is_configured or not self.device_id or not paths:
            return []

        out: list[dict[str, Any]] = []
        for idx in range(0, len(paths), 16):
            batch = paths[idx : idx + 16]
            payload = {
                "devices": [
                    {
                        "deviceId": self.device_id,
                        "traits": [
                            {"path": path, "needSubscribe": True} for path in batch
                        ],
                    }
                ],
                "needParam": True,
            }
            with suppress(AqaraAppError):
                resp = await self._async_post("/app/v1.0/lumi/app/qlink/trait/read", payload)
                if resp.get("code") == 0:
                    result = resp.get("result")
                    if isinstance(result, list) and result and isinstance(result[0], dict):
                        traits = result[0].get("traits")
                        if isinstance(traits, list):
                            out.extend(t for t in traits if isinstance(t, dict))
        return out

    async def _async_query_named_resources(
        self, options: list[str]
    ) -> dict[str, Any]:
        """Query named resources (e.g. set_video) via POST /app/v1.0/lumi/res/query."""
        if not self.device_id or not options:
            return {}
        payload = {"data": [{"options": options, "subjectId": self.device_id}]}
        with suppress(AqaraAppError):
            resp = await self._async_post("/app/v1.0/lumi/res/query", payload)
            if resp.get("code") == 0:
                return _extract_named_values(resp.get("result"))
        return {}

    async def async_query_app_view_detail(self) -> dict[str, Any]:
        """Query the Aqara App detail view (/app/v1.0/lumi/app/view/data/query)."""
        if not self.device_id:
            return {}
        query = f"options=&subjectId={self.device_id}&viewId=detail"
        with suppress(AqaraAppError):
            resp = await self._async_get(
                "/app/v1.0/lumi/app/view/data/query", query
            )
            if resp.get("code") == 0:
                return _extract_named_values(resp.get("result"))
        return {}

    async def _write_via_mode(
        self, mode: str, resource_id: str, value: str | int
    ) -> tuple[bool, str]:
        """Write a single resource using a specific Aqara API endpoint/payload mode."""
        val_str = str(value)
        mode_map: dict[str, tuple[str, dict[str, Any]]] = {
            "view_write": (
                "/app/v1.0/lumi/app/view/write",
                {
                    "data": {resource_id: val_str},
                    "options": "",
                    "subjectId": self.device_id,
                    "version": "6.53",
                    "viewId": "detail",
                },
            ),
            "res_write_simple": (
                "/app/v1.0/lumi/res/write",
                {
                    "data": {resource_id: value},
                    "subjectId": self.device_id,
                },
            ),
            "res_write_view": (
                "/app/v1.0/lumi/res/write",
                {
                    "data": {resource_id: val_str},
                    "options": "",
                    "subjectId": self.device_id,
                    "viewId": "detail",
                },
            ),
            "res_write_list": (
                "/app/v1.0/lumi/res/write",
                {
                    "data": [
                        {
                            "subjectId": self.device_id,
                            "resources": [
                                {"resourceId": resource_id, "value": val_str}
                            ],
                        }
                    ]
                },
            ),
        }
        path, payload = mode_map[mode]
        try:
            resp = await self._async_post(path, payload)
        except AqaraAppError as err:
            return False, str(err)
        if resp.get("code") == 0:
            return True, ""
        return (
            False,
            f"{resource_id} ({mode}): code={resp.get('code')} "
            f"msg={resp.get('message') or resp.get('msg') or 'rejected'}",
        )

    async def _async_write_single_resource(
        self, resource_id: str, value: str | int
    ) -> tuple[bool, str]:
        """Write a single resource value via Aqara App API, returning (ok, error_detail)."""
        if not self.device_id:
            return False, "device_id mancante"

        modes = ["view_write", "res_write_simple", "res_write_view", "res_write_list"]
        if self._write_mode and self._write_mode in modes:
            modes.sort(key=lambda m: m != self._write_mode)

        last_err = ""
        for mode in modes:
            ok, err = await self._write_via_mode(mode, resource_id, value)
            if ok:
                self._write_mode = mode
                return True, ""
            last_err = err
        return False, last_err

    async def _read_privacy_snapshot(
        self,
    ) -> tuple[dict[str, str], dict[str, Any], Any]:
        """Read 14.125.85, set_video named prop, and CameraActiveStatus."""
        res_entries, named_map, traits = await asyncio.gather(
            self._async_query_resources(["14.125.85"]),
            self._async_query_named_resources([RID_PRIVACY_SET_VIDEO]),
            self.async_query_traits([TRAIT_G100_ACTIVE]),
        )
        res_map = {
            str(it.get("resourceId") or it.get("resId") or ""): str(it.get("value"))
            for it in res_entries
            if (it.get("resourceId") or it.get("resId")) and it.get("value") is not None
        }
        active_val = _extract_trait_value(traits, TRAIT_G100_ACTIVE)
        return res_map, named_map, active_val

    async def _write_first_accepted(
        self,
        target_key: str,
        write_val: str | int,
        modes: tuple[str, ...],
        errors: list[str],
        accepted_cmds: list[str],
    ) -> str | None:
        """Try write modes in order and stop as soon as one succeeds (code=0)."""
        for mode in modes:
            ok, err = await self._write_via_mode(mode, target_key, write_val)
            if ok:
                accepted_cmds.append(f"{target_key}={write_val}({mode})")
                return mode
            if err:
                errors.append(err)
        return None

    async def async_set_camera_sleep(self, sleep_enabled: bool) -> bool:
        """Set Privacy Mode (14.125.85 + set_video) quickly without reboot/legacy RIDs."""
        if not self.is_configured or not self.device_id:
            return False

        sleep_str = "1" if sleep_enabled else "0"
        video_int = 0 if sleep_enabled else 1
        video_str = str(video_int)
        desired_active = not sleep_enabled

        pre_res, pre_named, pre_active = await self._read_privacy_snapshot()
        errors: list[str] = []
        accepted_cmds: list[str] = []

        # Fast path: fire 14.125.85 and set_video without sleeping 0.9s between modes.
        if not sleep_enabled:
            tasks: list[Awaitable[str | None]] = []
            if pre_res.get("14.125.85") != "0":
                tasks.append(
                    self._write_first_accepted(
                        "14.125.85",
                        "0",
                        ("view_write", "res_write_view", "res_write_simple"),
                        errors,
                        accepted_cmds,
                    )
                )
            tasks.append(
                self._write_first_accepted(
                    RID_PRIVACY_SET_VIDEO,
                    video_int,
                    ("res_write_simple",),
                    errors,
                    accepted_cmds,
                )
            )
            results = await asyncio.gather(*tasks)
            video_mode = results[-1]
            if video_mode is None:
                video_mode = await self._write_first_accepted(
                    RID_PRIVACY_SET_VIDEO,
                    video_str,
                    ("res_write_simple", "res_write_view", "view_write"),
                    errors,
                    accepted_cmds,
                )
        else:
            video_mode = await self._write_first_accepted(
                "14.125.85",
                sleep_str,
                ("view_write", "res_write_view", "res_write_simple"),
                errors,
                accepted_cmds,
            )
            if video_mode is None:
                video_mode = await self._write_first_accepted(
                    RID_PRIVACY_SET_VIDEO,
                    video_int,
                    ("res_write_simple",),
                    errors,
                    accepted_cmds,
                )
                if video_mode is None:
                    video_mode = await self._write_first_accepted(
                        RID_PRIVACY_SET_VIDEO,
                        video_str,
                        ("res_write_simple", "res_write_view", "view_write"),
                        errors,
                        accepted_cmds,
                    )

        if accepted_cmds:
            await asyncio.sleep(0.5)
            post_res, post_named, post_active = await self._read_privacy_snapshot()

            active_matches = (
                post_active is not None
                and _is_truthy_value(post_active) == desired_active
            )
            video_key_changed = (
                post_named.get(RID_PRIVACY_SET_VIDEO) is not None
                and _is_truthy_value(post_named[RID_PRIVACY_SET_VIDEO]) == desired_active
                and (
                    pre_named.get(RID_PRIVACY_SET_VIDEO) is None
                    or _is_truthy_value(pre_named.get(RID_PRIVACY_SET_VIDEO))
                    != desired_active
                )
            )

            if sleep_enabled and (
                active_matches
                or post_res.get("14.125.85") == "1"
                or video_key_changed
            ):
                self._sleep_rid = "14.125.85" if post_res.get("14.125.85") == "1" else RID_PRIVACY_SET_VIDEO
                if video_mode:
                    self._write_mode = video_mode
                return True

            if not sleep_enabled and not active_matches:
                # Cloud trait 2.136.32930 can take ~1s after wake; check once more.
                await asyncio.sleep(0.8)
                post_res, post_named, post_active = await self._read_privacy_snapshot()
                active_matches = (
                    post_active is not None
                    and _is_truthy_value(post_active) == desired_active
                )
                video_key_changed = (
                    post_named.get(RID_PRIVACY_SET_VIDEO) is not None
                    and _is_truthy_value(post_named[RID_PRIVACY_SET_VIDEO])
                    == desired_active
                    and (
                        pre_named.get(RID_PRIVACY_SET_VIDEO) is None
                        or _is_truthy_value(pre_named.get(RID_PRIVACY_SET_VIDEO))
                        != desired_active
                    )
                )

            if not sleep_enabled and (
                active_matches
                or (video_key_changed and (post_active is None or _is_truthy_value(post_active)))
            ):
                self._sleep_rid = RID_PRIVACY_SET_VIDEO
                if video_mode:
                    self._write_mode = video_mode
                return True

            if not sleep_enabled and post_active is not None and not _is_truthy_value(post_active):
                errors.append(
                    f"wake ({accepted_cmds[-1]}): accettato ma CameraActiveStatus={post_active}"
                )

        detail = (
            "; ".join(errors[-3:])
            if errors
            else (
                f"nessuna conferma stato (attivo={pre_active}, accettati={accepted_cmds[-2:]})"
            )
        )
        raise AqaraAppError(
            f"Il firmware della G100 ({self.device_id}) non ha completato il comando Privacy App: {detail}"
        )

    async def async_set_spotlight(self, enabled: bool) -> bool:
        """Turn the G100 built-in LED spotlight/flash (14.158.85) on or off."""
        if not self.is_configured:
            raise AqaraAppError(
                "Account Aqara non configurato: inserisci email e password "
                "nelle Opzioni per controllare il Flash / Faretto."
            )
        if not self.device_id:
            raise AqaraAppError(
                "ID dispositivo Aqara (lumi3.xxx) non rilevato: controlla le Opzioni."
            )
        ok, err = await self._async_write_single_resource(
            RID_SPOTLIGHT, "1" if enabled else "0"
        )
        if ok:
            return True
        raise AqaraAppError(
            f"Il firmware della G100 ({self.device_id}) non ha accettato il comando Flash: {err}"
        )

    async def async_set_status_led(self, enabled: bool) -> bool:
        """Toggle the camera status LED via G100 14.21.85 (2878/2879) or 8.0.2032."""
        if not self.is_configured:
            raise AqaraAppError(
                "Account Aqara non configurato: inserisci email e password "
                "nelle Opzioni dell'integrazione per controllare il LED di Stato."
            )
        if not self.device_id:
            raise AqaraAppError(
                "ID dispositivo Aqara (lumi3.xxx) non rilevato: controlla le Opzioni."
            )

        # Read current values before toggling so we can verify actual state change.
        before_period: str | None = None
        before_legacy: str | None = None
        with suppress(AqaraAppError):
            pre_entries = await self._async_query_resources(
                [RID_STATUS_LED_PACKED_PERIOD, "8.0.2032"]
            )
            for item in pre_entries:
                rid = str(item.get("resourceId") or "")
                if rid == RID_STATUS_LED_PACKED_PERIOD:
                    before_period = str(item.get("value"))
                elif rid == "8.0.2032":
                    before_legacy = str(item.get("value"))

        errors: list[str] = []
        period_candidates: list[str] = [
            encode_packed_led_period(
                turn_off_enabled=not enabled, start_min=0, end_min=1439
            )
        ]
        if before_period is not None:
            with suppress(TypeError, ValueError):
                _, s_min, e_min = decode_packed_led_period(before_period)
                preserved_val = encode_packed_led_period(
                    turn_off_enabled=not enabled, start_min=s_min, end_min=e_min
                )
                if preserved_val not in period_candidates:
                    period_candidates.append(preserved_val)
        g3_val = "5162041" if enabled else "2212920"
        if g3_val not in period_candidates:
            period_candidates.append(g3_val)

        target_legacy = "1" if enabled else "0"

        # If we already know 8.0.2032 works as the real RID, try it directly first.
        if self._led_rid and self._led_rid != RID_STATUS_LED_PACKED_PERIOD:
            ok, err = await self._async_write_single_resource(
                self._led_rid, target_legacy
            )
            if ok:
                return True
            if err:
                errors.append(err)

        # Try 14.21.85 across encodings & modes and verify readback actually updated.
        accepted_no_change = False
        for period_val in period_candidates:
            for mode in ("view_write", "res_write_view", "res_write_simple"):
                ok, err = await self._write_via_mode(
                    mode, RID_STATUS_LED_PACKED_PERIOD, period_val
                )
                if not ok:
                    if err:
                        errors.append(err)
                    continue
                await asyncio.sleep(0.8)
                post_entries = await self._async_query_resources(
                    [RID_STATUS_LED_PACKED_PERIOD]
                )
                after_period = next(
                    (
                        str(it.get("value"))
                        for it in post_entries
                        if str(it.get("resourceId") or "")
                        == RID_STATUS_LED_PACKED_PERIOD
                    ),
                    None,
                )
                if after_period == period_val or (
                    before_period is not None
                    and after_period is not None
                    and after_period != before_period
                ):
                    self._led_rid = RID_STATUS_LED_PACKED_PERIOD
                    return True
                accepted_no_change = True

        # Fallback: try 8.0.2032, device_night_tip_light, and other legacy LED RIDs
        legacy_ops: list[tuple[str, str | int]] = [
            (rid, target_legacy) for rid in RID_STATUS_LED_LIST
        ] + [
            (RID_LED_NIGHT_TIP, 1 if enabled else 0),
            (RID_LED_NIGHT_TIP, target_legacy),
        ]
        for rid, write_val in legacy_ops:
            for mode in ("view_write", "res_write_simple", "res_write_view", "res_write_list"):
                ok, err = await self._write_via_mode(mode, rid, write_val)
                if not ok:
                    if err:
                        errors.append(err)
                    continue
                if rid in ("8.0.2032", RID_LED_NIGHT_TIP):
                    await asyncio.sleep(0.6)
                    post_entries = await self._async_query_resources(["8.0.2032"])
                    after_legacy = next(
                        (
                            str(it.get("value"))
                            for it in post_entries
                            if str(it.get("resourceId") or "") == "8.0.2032"
                        ),
                        None,
                    )
                    if after_legacy == str(write_val) or (
                        before_legacy is not None
                        and after_legacy is not None
                        and after_legacy != before_legacy
                    ):
                        self._led_rid = rid
                        return True
                    accepted_no_change = True
                else:
                    self._led_rid = rid
                    return True

        if accepted_no_change:
            raise AqaraAppError(
                f"Il server Aqara ha accettato la richiesta ma la G100 ({self.device_id}) "
                f"non ha cambiato stato (14.21.85={before_period}, 8.0.2032={before_legacy})."
            )

        raise AqaraAppError(
            f"Il firmware della G100 ({self.device_id}) non ha accettato il comando LED "
            f"({'; '.join(errors[-2:]) if errors else 'sconosciuto'})."
        )

    async def async_set_resource(
        self, resource_id: str, value: str | int, label: str = ""
    ) -> bool:
        """Write an arbitrary G100 config resource (switch/select/number) via Aqara App API."""
        if not self.is_configured:
            raise AqaraAppError(
                "Account Aqara non configurato: inserisci email e password "
                f"nelle Opzioni per modificare {label or resource_id}."
            )
        if not self.device_id:
            raise AqaraAppError(
                "ID dispositivo Aqara (lumi3.xxx) non rilevato: controlla le Opzioni."
            )
        ok, err = await self._async_write_single_resource(resource_id, str(value))
        if ok:
            return True
        raise AqaraAppError(
            f"Il firmware della G100 ({self.device_id}) non ha accettato {label or resource_id}: {err}"
        )

    def _update_event_window(
        self,
        *,
        kind: str,
        val: Any,
        ts_raw: Any,
        now: float,
    ) -> None:
        """Track edge-triggered AI/motion/sound pulses uniformly with a 30s hold window."""
        ts_ms = 0
        with suppress(TypeError, ValueError):
            ts_ms = int(float(ts_raw or 0))
        ts_sec = ts_ms / 1000.0 if ts_ms > 1_000_000_000_000 else float(ts_ms)
        recent_ts = ts_sec > 0 and (time.time() - ts_sec) <= MOTION_HOLD_SECONDS

        prev_ts = self._event_ts.get(kind, 0)
        fresh_edge = (
            prev_ts > 0
            and ts_ms > prev_ts
            and ts_sec > 0
            and (time.time() - ts_sec) <= 300
        )
        if ts_ms > prev_ts:
            self._event_ts[kind] = ts_ms
            if (
                ts_sec > 0
                and kind in ("motion", "person", "pet", "package", "vehicle")
                and ts_ms >= self._last_motion_ts_ms
            ):
                self._last_motion_ts_ms = ts_ms
                self._last_motion_iso = _format_local_timestamp(ts_sec)
            if recent_ts or fresh_edge:
                self._event_until[kind] = now + MOTION_HOLD_SECONDS
                if kind in ("person", "pet", "package", "vehicle"):
                    self._event_until["motion"] = max(
                        self._event_until.get("motion", 0.0),
                        now + MOTION_HOLD_SECONDS,
                    )
        elif _is_truthy_value(val) and (ts_ms == 0 or recent_ts):
            self._event_until[kind] = now + MOTION_HOLD_SECONDS
            if ts_sec > 0 and not self._last_motion_iso:
                self._last_motion_ts_ms = max(self._last_motion_ts_ms, ts_ms)
                self._last_motion_iso = _format_local_timestamp(ts_sec)

    async def async_poll_device_resources(self) -> dict[str, Any]:
        """Poll sleep/privacy, status LED, config resources, and all V3 AI sensors."""
        now = time.monotonic()
        snapshot: dict[str, Any] = {
            "connected": False,
            "privacy_sleep": None,
            "status_led": None,
            "spotlight": None,
            "camera_active": None,
            "wifi_rssi": None,
            "motion_detected": now < self._event_until.get("motion", 0.0),
            "person_detected": now < self._event_until.get("person", 0.0),
            "pet_detected": now < self._event_until.get("pet", 0.0),
            "package_detected": now < self._event_until.get("package", 0.0),
            "vehicle_detected": now < self._event_until.get("vehicle", 0.0),
            "sound_detected": now < self._event_until.get("sound", 0.0),
            "last_motion_event": self._last_motion_iso,
            "resources": {},
        }
        if not self.is_configured or not self.device_id:
            return snapshot

        all_rids = list(
            RID_CAMERA_SLEEP_LIST
            + RID_PRIVACY_VIDEO_SWITCH_LIST
            + (RID_STATUS_LED_PACKED_PERIOD, RID_SPOTLIGHT)
            + RID_STATUS_LED_LIST
            + RID_MOTION_LIST
            + RID_PERSON_LIST
            + RID_SOUND_LIST
            + G100_POLL_CONFIG_RIDS
        )
        entries, named_map, traits = await asyncio.gather(
            self._async_query_resources(all_rids),
            self._async_query_named_resources([RID_PRIVACY_SET_VIDEO]),
            self.async_query_traits(list(G100_POLL_TRAITS)),
        )
        snapshot["connected"] = bool(entries or named_map or traits or self.token)

        sleep_rid_val: bool | None = None
        video_switch_val: bool | None = None
        if named_map.get(RID_PRIVACY_SET_VIDEO) is not None:
            video_switch_val = _is_truthy_value(named_map[RID_PRIVACY_SET_VIDEO])

        resources_map: dict[str, str] = {}
        for item in entries:
            rid = str(item.get("resourceId") or item.get("resId") or "")
            val = item.get("value")
            ts = item.get("timeStamp") or item.get("timestamp")
            if not rid or val is None:
                continue

            if rid in G100_POLL_CONFIG_RIDS:
                resources_map[rid] = str(val)

            if rid == "14.125.85":
                sleep_rid_val = _is_truthy_value(val)
            elif rid in RID_CAMERA_SLEEP_LIST and sleep_rid_val is None:
                sleep_rid_val = _is_truthy_value(val)
            elif rid in RID_PRIVACY_VIDEO_SWITCH_LIST:
                video_switch_val = _is_truthy_value(val)
            elif rid == RID_STATUS_LED_PACKED_PERIOD:
                with suppress(TypeError, ValueError):
                    if self._led_rid is None:
                        self._led_rid = rid
                    _, s_min, e_min = decode_packed_led_period(val)
                    self._led_period_range = (s_min, e_min)
                    snapshot["status_led"] = is_led_currently_enabled_from_period(val)
            elif rid == RID_SPOTLIGHT:
                snapshot["spotlight"] = _is_truthy_value(val)
            elif rid in RID_STATUS_LED_LIST:
                if self._led_rid is None:
                    self._led_rid = rid
                snapshot["status_led"] = _is_truthy_value(val)
            elif rid in RID_MOTION_LIST:
                self._update_event_window(kind="motion", val=val, ts_raw=ts, now=now)
            elif rid in RID_PERSON_LIST:
                self._update_event_window(kind="person", val=val, ts_raw=ts, now=now)
            elif rid in RID_SOUND_LIST:
                self._update_event_window(kind="sound", val=val, ts_raw=ts, now=now)

        trait_kind_map = {
            TRAIT_G100_OCCUPANCY: "motion",
            TRAIT_G100_PERSON: "person",
            TRAIT_G100_PET: "pet",
            TRAIT_G100_PACKAGE: "package",
            TRAIT_G100_VEHICLE: "vehicle",
        }
        trait_active: bool | None = None
        for trait in traits:
            path = str(trait.get("path") or "")
            val = trait.get("value")
            ts = trait.get("traitTime") or trait.get("timeStamp")
            if not path:
                continue
            if path == TRAIT_G100_ACTIVE and val is not None:
                trait_active = _is_truthy_value(val)
                snapshot["camera_active"] = trait_active
            elif path == TRAIT_G100_WIFI_RSSI and val is not None:
                with suppress(TypeError, ValueError):
                    snapshot["wifi_rssi"] = int(float(val))
            elif path in trait_kind_map:
                self._update_event_window(
                    kind=trait_kind_map[path], val=val, ts_raw=ts, now=now
                )

        # Unify real Privacy / Sleep status across 14.125.85, set_video/14.74.85, and CameraActiveStatus (2.136.32930)
        if sleep_rid_val is True or video_switch_val is False or trait_active is False:
            snapshot["privacy_sleep"] = True
        elif video_switch_val is True or trait_active is True or sleep_rid_val is False:
            snapshot["privacy_sleep"] = False

        snapshot["motion_detected"] = now < self._event_until.get("motion", 0.0)
        snapshot["person_detected"] = now < self._event_until.get("person", 0.0)
        snapshot["pet_detected"] = now < self._event_until.get("pet", 0.0)
        snapshot["package_detected"] = now < self._event_until.get("package", 0.0)
        snapshot["vehicle_detected"] = now < self._event_until.get("vehicle", 0.0)
        snapshot["sound_detected"] = now < self._event_until.get("sound", 0.0)
        snapshot["last_motion_event"] = self._last_motion_iso
        snapshot["resources"] = resources_map
        return snapshot

    async def async_run_full_diagnostic(self, camera_ip: str) -> str:
        """Run a full local mDNS + TCP ports + Aqara resource/trait/view diagnostic scan."""
        mdns_task = async_query_aqara_mdns(camera_ip, timeout=1.5)
        ports_task = async_scan_tcp_ports(camera_ip)
        mdns_info, port_map = await asyncio.gather(mdns_task, ports_task)

        lines: list[str] = [
            f"### Scansione Tecnica Aqara G100 (`{camera_ip}`)",
            "",
            "**1. Rilevamento Locale mDNS (`UDP 5353`)**",
            f"- Hostname: `{mdns_info.get('hostname') or 'N/D'}`",
            f"- ID Reale (`id`): `{mdns_info.get('device_id') or 'N/D'}`",
            f"- Modello (`md`): `{mdns_info.get('model') or 'N/D'}`",
            f"- Firmware (`fw`): `{mdns_info.get('firmware') or 'N/D'}`",
            f"- Flag TXT: `{json.dumps(mdns_info.get('txt') or {})}`",
            f"- Porte SRV mDNS: `{json.dumps(mdns_info.get('srv_ports') or {})}`",
            "",
            "**2. Porte TCP Aperte in LAN**",
            "- "
            + ", ".join(
                f"`{p}`: {'APERTO' if open_ else 'chiuso'}"
                for p, open_ in sorted(port_map.items())
            ),
            "",
            "**3. Stato Account & Risorse Aqara App**",
            f"- Configurato: `{self.is_configured}` | Regione: `{self.region}`",
            f"- Device ID attivo nel client: `{self.device_id or 'non impostato'}`",
        ]

        if not self.is_configured:
            lines.append("- Account Aqara non configurato nelle Opzioni.")
            return "\n".join(lines)

        if mdns_info.get("device_id") and _looks_like_legacy_mac_id(self.device_id):
            self.device_id = str(mdns_info["device_id"])
            lines.append(
                f"- ID aggiornato automaticamente da mDNS -> `{self.device_id}`"
            )

        devices = await self.async_list_account_devices()
        lines.append(f"- Dispositivi trovati nell'account Aqara: `{len(devices)}`")
        for dev in devices:
            did = str(dev.get("did") or "")
            model = str(dev.get("model") or "")
            if "agl005" in model.lower() or "camera" in model.lower() or did == self.device_id:
                lines.append(
                    f"  - `{dev.get('deviceName') or did}` (`{did}`, `{model}`, "
                    f"fw=`{dev.get('firmwareVersion')}`, state=`{dev.get('state')}`)"
                )

        if self.device_id:
            rids_only = [rid for rid, _ in G100_DIAGNOSTIC_RIDS]
            desc_map = dict(G100_DIAGNOSTIC_RIDS)
            named_opts = [opt for opt, _ in G100_NAMED_DIAGNOSTIC_OPTIONS]
            named_desc = dict(G100_NAMED_DIAGNOSTIC_OPTIONS)

            res_entries, named_map, trait_entries, view_map = await asyncio.gather(
                self._async_query_resources(rids_only),
                self._async_query_named_resources(named_opts),
                self.async_query_traits(list(G100_DIAGNOSTIC_TRAITS)),
                self.async_query_app_view_detail(),
            )

            lines.append(f"- Risorse RID lette (`res/query/by/resourceId`): `{len(res_entries)}`")
            for item in res_entries:
                rid = str(item.get("resourceId") or item.get("resId") or "?")
                lines.append(
                    f"  - `{rid}` ({desc_map.get(rid, 'RID')}): `{item.get('value')}`"
                )

            lines.append(f"- Risorse Nominate (`res/query` options): `{len(named_map)}`")
            for k, v in named_map.items():
                lines.append(
                    f"  - `{k}` ({named_desc.get(k, 'Option')}): `{v}`"
                )

            lines.append(f"- Vista App Detail (`app/view/data/query`): `{len(view_map)}`")
            for k, v in sorted(view_map.items()):
                val_txt = str(v)
                if len(val_txt) > 90:
                    val_txt = val_txt[:87] + "..."
                lines.append(f"  - `{k}`: `{val_txt}`")

            lines.append(f"- Trait V3 letti (`qlink/trait/read`): `{len(trait_entries)}`")
            for tr in trait_entries:
                lines.append(
                    f"  - `{tr.get('path')}`: value=`{tr.get('value')}`, "
                    f"time=`{tr.get('traitTime')}`"
                )

        return "\n".join(lines)
