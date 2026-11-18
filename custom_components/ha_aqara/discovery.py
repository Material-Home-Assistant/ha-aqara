"""Automatic local LAN discovery, mDNS Aqara ID resolution, and ARP lookup."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import ipaddress
import logging
from pathlib import Path
import re
import socket
import struct
import time
from typing import Any

from .const import (
    DEFAULT_RTSP_PORT,
    DEFAULT_TALKBACK_TCP_PORT,
    DISCOVERY_CONCURRENCY,
    DISCOVERY_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)
_MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")

_MDNS_PORT = 5353
_DNS_TYPE_A = 1
_DNS_TYPE_PTR = 12
_DNS_TYPE_TXT = 16
_DNS_TYPE_SRV = 33
_AQARA_SERVICE_NAMES = (b"_aqara._tcp.local", b"_aqara-setup._tcp.local")


def _build_mdns_query() -> bytes:
    """Build a standard mDNS query for _aqara._tcp.local and _aqara-setup._tcp.local."""
    header = struct.pack("!HHHHHH", 0, 0, 2, 0, 0, 0)
    q1 = b"\x06_aqara\x04_tcp\x05local\x00" + struct.pack("!HH", _DNS_TYPE_PTR, 1)
    q2 = b"\x0c_aqara-setup\x04_tcp\x05local\x00" + struct.pack("!HH", _DNS_TYPE_PTR, 1)
    return header + q1 + q2


def _read_dns_name(packet: bytes, offset: int) -> tuple[str, int]:
    """Decode a DNS/mDNS domain name handling RFC 1035 compression pointers."""
    labels: list[str] = []
    jumped = False
    orig_next = offset
    seen_offsets: set[int] = set()

    while offset < len(packet):
        if offset in seen_offsets:
            break
        seen_offsets.add(offset)
        length = packet[offset]
        if length == 0:
            if not jumped:
                orig_next = offset + 1
            break
        if (length & 0xC0) == 0xC0:
            if offset + 1 >= len(packet):
                break
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            if not jumped:
                orig_next = offset + 2
            jumped = True
            offset = pointer
            continue
        offset += 1
        end = offset + length
        if end > len(packet):
            break
        labels.append(packet[offset:end].decode("utf-8", errors="replace"))
        offset = end
        if not jumped:
            orig_next = offset

    return ".".join(labels), orig_next


def _parse_txt_rdata(rdata: bytes) -> dict[str, str]:
    """Parse mDNS TXT key=value pairs."""
    props: dict[str, str] = {}
    pos = 0
    while pos < len(rdata):
        length = rdata[pos]
        pos += 1
        if length == 0 or pos + length > len(rdata):
            break
        entry = rdata[pos : pos + length].decode("utf-8", errors="replace")
        pos += length
        if "=" in entry:
            key, value = entry.split("=", 1)
            props[key] = value
        else:
            props[entry] = ""
    return props


def parse_aqara_mdns_packet(packet: bytes) -> dict[str, Any]:
    """Parse a raw mDNS response packet from an Aqara device into metadata."""
    if len(packet) < 12:
        return {}

    _, _, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", packet[:12])
    offset = 12

    for _ in range(qdcount):
        if offset >= len(packet):
            return {}
        _, offset = _read_dns_name(packet, offset)
        offset += 4  # qtype (2) + qclass (2)

    txt: dict[str, str] = {}
    srv_ports: dict[str, int] = {}
    hostname = ""
    ipv4_addr = ""

    total_rrs = ancount + nscount + arcount
    for _ in range(total_rrs):
        if offset >= len(packet):
            break
        rr_name, offset = _read_dns_name(packet, offset)
        if offset + 10 > len(packet):
            break
        rr_type, _, _, rdlength = struct.unpack("!HHIH", packet[offset : offset + 10])
        offset += 10
        rdata = packet[offset : offset + rdlength]
        offset += rdlength
        if len(rdata) < rdlength:
            break

        if rr_type == _DNS_TYPE_TXT:
            parsed = _parse_txt_rdata(rdata)
            # Merge keeping non-empty values; _aqara._tcp carries fw/md/le/pw/id
            for k, v in parsed.items():
                if k not in txt or "md" in parsed:
                    txt[k] = v
        elif rr_type == _DNS_TYPE_SRV and len(rdata) >= 6:
            _, _, port = struct.unpack("!HHH", rdata[:6])
            target, _ = _read_dns_name(packet, offset - rdlength + 6)
            if "_aqara-setup" in rr_name:
                srv_ports["aqara_setup"] = port
            elif "_aqara" in rr_name:
                srv_ports["aqara"] = port
            if target and not hostname:
                hostname = target.rstrip(".")
        elif rr_type == _DNS_TYPE_A and len(rdata) == 4 and not ipv4_addr:
            ipv4_addr = socket.inet_ntoa(rdata)

    if not txt and not srv_ports:
        return {}

    return {
        "device_id": txt.get("id", ""),
        "model": txt.get("md", ""),
        "firmware": txt.get("fw", ""),
        "hostname": hostname,
        "ip": ipv4_addr,
        "srv_ports": srv_ports,
        "txt": txt,
    }


def _merge_mdns_info(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge partial mDNS responses from the same Aqara device."""
    if not base:
        return dict(incoming)
    base.setdefault("txt", {}).update(incoming.get("txt", {}))
    base.setdefault("srv_ports", {}).update(incoming.get("srv_ports", {}))
    for key in ("device_id", "model", "firmware", "hostname", "ip"):
        if incoming.get(key) and not base.get(key):
            base[key] = incoming[key]
    return base


def _source_ip_for_target(target_ip: str) -> str | None:
    """Return local IPv4 interface address used to reach target_ip."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((target_ip, _MDNS_PORT))
        ip = str(probe.getsockname()[0])
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        return None
    finally:
        probe.close()
    return None


def _query_aqara_mdns_sync(ip: str, timeout: float) -> dict[str, Any]:
    """Query mDNS via both interface-bound multicast (224.0.0.251:5353) and unicast."""
    query = _build_mdns_query()
    local_ip = _source_ip_for_target(ip) or "0.0.0.0"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    multicast_joined = False
    try:
        with suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                with suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", _MDNS_PORT))
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_IF,
                socket.inet_aton(local_ip),
            )
            mreq = socket.inet_aton("224.0.0.251") + socket.inet_aton(local_ip)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            multicast_joined = True
    except OSError:
        pass

    merged: dict[str, Any] = {}
    try:
        with suppress(OSError):
            sock.sendto(query, (ip, _MDNS_PORT))
        if multicast_joined:
            with suppress(OSError):
                sock.sendto(query, ("224.0.0.251", _MDNS_PORT))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(4096)
            except (TimeoutError, OSError):
                break
            if addr[0] != ip:
                continue
            parsed = parse_aqara_mdns_packet(data)
            if not parsed:
                continue
            if not merged:
                merged = parsed
            else:
                merged.setdefault("txt", {}).update(parsed.get("txt", {}))
                merged.setdefault("srv_ports", {}).update(parsed.get("srv_ports", {}))
                for key in ("device_id", "model", "firmware", "hostname"):
                    if parsed.get(key) and not merged.get(key):
                        merged[key] = parsed[key]
            if merged.get("device_id") and merged.get("model"):
                break
    except OSError:
        return {}
    finally:
        sock.close()
    return merged


def _discover_mdns_all_sync(
    local_ips: list[str], timeout: float
) -> dict[str, dict[str, Any]]:
    """Broadcast mDNS query across local interfaces and collect Aqara responses by IP."""
    query = _build_mdns_query()
    ifaces = local_ips or ["0.0.0.0"]
    found: dict[str, dict[str, Any]] = {}

    for iface_ip in ifaces:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        multicast_joined = False
        try:
            with suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    with suppress(OSError):
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                sock.bind(("", _MDNS_PORT))
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(iface_ip),
                )
                mreq = socket.inet_aton("224.0.0.251") + socket.inet_aton(iface_ip)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                multicast_joined = True
        except OSError:
            pass

        try:
            with suppress(OSError):
                sock.sendto(query, ("224.0.0.251", _MDNS_PORT))
            if not multicast_joined:
                with suppress(OSError):
                    sock.bind(("", 0))
                    sock.sendto(query, ("224.0.0.251", _MDNS_PORT))

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = max(0.05, deadline - time.monotonic())
                sock.settimeout(remaining)
                try:
                    data, addr = sock.recvfrom(4096)
                except (TimeoutError, OSError):
                    break
                parsed = parse_aqara_mdns_packet(data)
                if not parsed:
                    continue
                cam_ip = str(parsed.get("ip") or addr[0])
                found[cam_ip] = _merge_mdns_info(found.get(cam_ip, {}), parsed)
        except OSError:
            pass
        finally:
            sock.close()

    return found


async def async_query_aqara_mdns(ip: str, timeout: float = 1.2) -> dict[str, Any]:
    """Query Aqara mDNS records (_aqara._tcp / _aqara-setup._tcp) directly at ip:5353."""
    clean_ip = ip.strip()
    if not clean_ip:
        return {}
    return await asyncio.to_thread(_query_aqara_mdns_sync, clean_ip, timeout)


async def async_scan_tcp_ports(
    ip: str,
    ports: tuple[int, ...] = (8554, 10099, 18641, 54324, 34318, 37121, 443),
    timeout: float = 0.6,
) -> dict[int, bool]:
    """Check which diagnostic TCP ports are currently open on the camera."""
    results = await asyncio.gather(
        *(_async_check_port(ip, port, timeout) for port in ports)
    )
    return dict(zip(ports, results, strict=True))


@dataclass(frozen=True, slots=True)
class DiscoveredCamera:
    """Candidate camera discovered on the local network."""

    ip: str
    rtsp_port: int
    talkback_port_open: bool
    rtsp_confirmed: bool
    latency_ms: float
    server_header: str = ""
    device_id: str = ""
    model: str = ""
    firmware: str = ""
    hostname: str = ""
    mdns_txt: dict[str, str] = field(default_factory=dict)

    @property
    def is_aqara_signature(self) -> bool:
        """Return True if Aqara mDNS or RTSP+P2P (8554 + 54324) signature matches."""
        return bool(self.device_id or self.talkback_port_open)

    @property
    def label(self) -> str:
        """Format a clear UI label for the discovery picker."""
        if self.device_id:
            fw = f" FW {self.firmware}" if self.firmware else ""
            return (
                f"Aqara G100 ({self.ip}){fw} — ID {self.device_id} "
                f"({self.latency_ms} ms)"
            )
        if self.is_aqara_signature:
            return (
                f"Aqara G100 ({self.ip}) — RTSP {self.rtsp_port} + "
                f"P2P {DEFAULT_TALKBACK_TCP_PORT} ({self.latency_ms} ms)"
            )
        extra = f" [{self.server_header}]" if self.server_header else ""
        return f"Camera RTSP ({self.ip}:{self.rtsp_port}){extra} ({self.latency_ms} ms)"


def format_lumi_device_id(mac: str) -> str:
    """Convert a MAC address like 54:EF:44:AA:BB:CC into lumi1.54ef44aabbcc."""
    clean_mac = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    if len(clean_mac) != 12:
        return ""
    return f"lumi1.{clean_mac}"


def _read_proc_net_arp(target_ip: str) -> str | None:
    """Read Linux /proc/net/arp synchronously for a matching MAC address."""
    arp_path = Path("/proc/net/arp")
    if not arp_path.exists():
        return None
    with suppress(OSError):
        for line in arp_path.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[0] == target_ip:
                mac = parts[3]
                if mac != "00:00:00:00:00:00" and _MAC_RE.fullmatch(mac):
                    return mac.lower()
    return None


async def async_lookup_mac_for_ip(ip: str) -> str | None:
    """Resolve the MAC address of a local LAN IP using ARP tables."""
    clean_ip = ip.strip()
    if not clean_ip:
        return None

    mac = await asyncio.to_thread(_read_proc_net_arp, clean_ip)
    if mac:
        return mac

    for cmd in (["ip", "neigh", "show", clean_ip], ["arp", "-n", clean_ip], ["arp", "-a", clean_ip]):
        with suppress(OSError, TimeoutError):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
            text = stdout.decode("utf-8", errors="ignore")
            for line in text.splitlines():
                if clean_ip in line:
                    match = _MAC_RE.search(line)
                    if match and match.group(0).replace("-", ":") != "00:00:00:00:00:00":
                        return match.group(0).replace("-", ":").lower()
    return None


def _fallback_local_ip() -> str | None:
    """Determine primary local IPv4 address via standard library socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = str(sock.getsockname()[0])
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        sock.close()
    return None


async def _async_get_local_ipv4s(hass: Any = None) -> list[str]:
    """Collect Home Assistant's non-loopback LAN IPv4 addresses across adapters."""
    ips: list[str] = []
    if hass is not None:
        with suppress(Exception):
            from homeassistant.components import network

            adapters = await network.async_get_adapters(hass)
            for adapter in adapters:
                if not adapter.get("enabled", True):
                    continue
                for ip_info in adapter.get("ipv4", []):
                    addr = str(ip_info.get("address") or "")
                    if addr and not addr.startswith("127.") and addr not in ips:
                        ips.append(addr)

            target = getattr(network, "PUBLIC_TARGET_IP", "8.8.8.8")
            src = await network.async_get_source_ip(hass, target_ip=target)
            if src and not str(src).startswith("127.") and str(src) not in ips:
                ips.append(str(src))

    fallback = _fallback_local_ip()
    if fallback and fallback not in ips:
        ips.append(fallback)
    return ips


async def async_get_local_subnet_ips(hass: Any = None) -> list[str]:
    """Return candidate IPv4 host addresses across Home Assistant's local /24 subnets."""
    local_ips = await _async_get_local_ipv4s(hass)
    if not local_ips:
        return []

    # Exclude virtual container subnets (172.17..172.31) if real LAN subnets exist
    real_lan_ips = [ip for ip in local_ips if not ip.startswith("172.")]
    scan_ips = real_lan_ips if real_lan_ips else local_ips

    seen_hosts: list[str] = []
    seen_nets: set[str] = set()
    for local_ip in scan_ips:
        try:
            ip_obj = ipaddress.IPv4Address(local_ip)
        except ipaddress.AddressValueError:
            continue
        if ip_obj.is_loopback or ip_obj.is_link_local:
            continue
        net = ipaddress.IPv4Network(f"{ip_obj}/24", strict=False)
        if str(net) in seen_nets:
            continue
        seen_nets.add(str(net))
        for host in net.hosts():
            host_str = str(host)
            if host_str != local_ip:
                seen_hosts.append(host_str)
    return seen_hosts


async def _async_check_port(ip: str, port: int, timeout: float) -> bool:
    """Return True if a TCP port accepts connections."""
    writer: asyncio.StreamWriter | None = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


async def async_probe_aqara_g100_candidate(
    ip: str,
    rtsp_port: int = DEFAULT_RTSP_PORT,
    timeout: float = DISCOVERY_TIMEOUT,
    pre_mdns: dict[str, Any] | None = None,
) -> DiscoveredCamera | None:
    """Probe a single IP for Aqara G100 ports (8554 RTSP + 54324 P2P + 5353 mDNS)."""
    start = time.monotonic()
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    rtsp_confirmed = False
    rtsp_port_open = False
    server_header = ""
    latency_ms = 0.0

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, rtsp_port),
            timeout=timeout,
        )
        rtsp_port_open = True
        latency_ms = round((time.monotonic() - start) * 1000.0, 1)

        probe_req = (
            f"OPTIONS rtsp://{ip}:{rtsp_port}/ch1 RTSP/1.0\r\n"
            "CSeq: 1\r\n"
            "User-Agent: HomeAssistant-AqaraG100\r\n\r\n"
        ).encode("ascii")
        writer.write(probe_req)
        await writer.drain()

        with suppress(TimeoutError, OSError):
            banner = await asyncio.wait_for(reader.read(512), timeout=timeout)
            if b"RTSP/1." in banner:
                rtsp_confirmed = True
                for line in banner.decode("ascii", errors="ignore").splitlines():
                    if line.lower().startswith("server:"):
                        server_header = line.split(":", 1)[1].strip()
                        break
    except (OSError, TimeoutError):
        pass
    finally:
        if writer is not None:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    talkback_open = False
    mdns_info = dict(pre_mdns or {})
    if rtsp_port_open or mdns_info:
        talkback_open = await _async_check_port(ip, DEFAULT_TALKBACK_TCP_PORT, timeout)
        if not mdns_info:
            mdns_info = await async_query_aqara_mdns(ip, timeout=max(0.8, timeout))
    else:
        talkback_open = await _async_check_port(ip, DEFAULT_TALKBACK_TCP_PORT, timeout)
        if not talkback_open:
            return None
        latency_ms = round((time.monotonic() - start) * 1000.0, 1)
        mdns_info = await async_query_aqara_mdns(ip, timeout=max(0.8, timeout))

    if not rtsp_confirmed and not rtsp_port_open and not talkback_open and not mdns_info:
        return None

    return DiscoveredCamera(
        ip=ip,
        rtsp_port=rtsp_port,
        talkback_port_open=talkback_open,
        rtsp_confirmed=rtsp_confirmed,
        latency_ms=latency_ms,
        server_header=server_header,
        device_id=str(mdns_info.get("device_id", "")),
        model=str(mdns_info.get("model", "")),
        firmware=str(mdns_info.get("firmware", "")),
        hostname=str(mdns_info.get("hostname", "")),
        mdns_txt=dict(mdns_info.get("txt", {})),
    )


async def async_discover_cameras(
    hass: Any = None,
    rtsp_port: int = DEFAULT_RTSP_PORT,
    timeout: float = DISCOVERY_TIMEOUT,
    candidate_ips: list[str] | None = None,
) -> list[DiscoveredCamera]:
    """Scan local mDNS and LAN subnets concurrently for Aqara G100 / RTSP cameras."""
    local_ips = await _async_get_local_ipv4s(hass)
    mdns_task = asyncio.to_thread(_discover_mdns_all_sync, local_ips, 1.2)
    ips_task = (
        asyncio.sleep(0, result=candidate_ips)
        if candidate_ips is not None
        else async_get_local_subnet_ips(hass)
    )
    mdns_by_ip, scan_ips = await asyncio.gather(mdns_task, ips_task)

    merged_ips = list(dict.fromkeys([*mdns_by_ip.keys(), *(scan_ips or [])]))
    if not merged_ips:
        return []

    semaphore = asyncio.Semaphore(DISCOVERY_CONCURRENCY)

    async def _bounded_probe(ip: str) -> DiscoveredCamera | None:
        async with semaphore:
            return await async_probe_aqara_g100_candidate(
                ip,
                rtsp_port=rtsp_port,
                timeout=timeout,
                pre_mdns=mdns_by_ip.get(ip),
            )

    results = await asyncio.gather(*(_bounded_probe(ip) for ip in merged_ips))
    discovered = [cam for cam in results if cam is not None]
    discovered.sort(
        key=lambda cam: (
            not bool(cam.device_id),
            not cam.is_aqara_signature,
            not cam.rtsp_confirmed,
            cam.latency_ms,
        )
    )
    _LOGGER.debug("LAN discovery found %d candidate(s): %s", len(discovered), discovered)
    return discovered
