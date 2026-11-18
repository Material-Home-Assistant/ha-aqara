"""100% Local LAN Talkback (two-way audio speaker) client for Aqara Camera G100.

Implements Aqara's LmLocalPacket TCP control channel (port 54324) and
UDP RTP AAC-ADTS 16kHz mono audio stream (port 54323).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import random
import struct
import time

from .const import DEFAULT_TALKBACK_TCP_PORT, DEFAULT_TALKBACK_UDP_PORT

_LOGGER = logging.getLogger(__name__)

_MAGIC = b"\xFE\xEF"
_TYPE_START_VOICE = 0
_TYPE_STOP_VOICE = 1
_TYPE_ACK = 2
_TYPE_HEARTBEAT = 3
_RTP_PAYLOAD_TYPE = 97
_AAC_SAMPLES_PER_FRAME = 1024
_AAC_FRAME_INTERVAL = 0.064
_HEARTBEAT_INTERVAL = 5.0

_CRC16_TABLE = (
    0x0000, 0x1189, 0x2312, 0x329B, 0x4624, 0x57AD, 0x6536, 0x74BF,
    0x8C48, 0x9DC1, 0xAF5A, 0xBED3, 0xCA6C, 0xDBE5, 0xE97E, 0xF8F7,
    0x1081, 0x0108, 0x3393, 0x221A, 0x56A5, 0x472C, 0x75B7, 0x643E,
    0x9CC9, 0x8D40, 0xBFDB, 0xAE52, 0xDAED, 0xCB64, 0xF9FF, 0xE876,
    0x2102, 0x308B, 0x0210, 0x1399, 0x6726, 0x76AF, 0x4434, 0x55BD,
    0xAD4A, 0xBCC3, 0x8E58, 0x9FD1, 0xEB6E, 0xFAE7, 0xC87C, 0xD9F5,
    0x3183, 0x200A, 0x1291, 0x0318, 0x77A7, 0x662E, 0x54B5, 0x453C,
    0xBDCB, 0xAC42, 0x9ED9, 0x8F50, 0xFBEF, 0xEA66, 0xD8FD, 0xC974,
    0x4204, 0x538D, 0x6116, 0x709F, 0x0420, 0x15A9, 0x2732, 0x36BB,
    0xCE4C, 0xDFC5, 0xED5E, 0xFCD7, 0x8868, 0x99E1, 0xAB7A, 0xBAF3,
    0x5285, 0x430C, 0x7197, 0x601E, 0x14A1, 0x0528, 0x37B3, 0x263A,
    0xDECD, 0xCF44, 0xFDDF, 0xEC56, 0x98E9, 0x8960, 0xBBFB, 0xAA72,
    0x6306, 0x728F, 0x4014, 0x519D, 0x2522, 0x34AB, 0x0630, 0x17B9,
    0xEF4E, 0xFEC7, 0xCC5C, 0xDDD5, 0xA96A, 0xB8E3, 0x8A78, 0x9BF1,
    0x7387, 0x620E, 0x5095, 0x411C, 0x35A3, 0x242A, 0x16B1, 0x0738,
    0xFFCF, 0xEE46, 0xDCDD, 0xCD54, 0xB9EB, 0xA862, 0x9AF9, 0x8B70,
    0x8408, 0x9581, 0xA71A, 0xB693, 0xC22C, 0xD3A5, 0xE13E, 0xF0B7,
    0x0840, 0x19C9, 0x2B52, 0x3ADB, 0x4E64, 0x5FED, 0x6D76, 0x7CFF,
    0x9489, 0x8500, 0xB79B, 0xA612, 0xD2AD, 0xC324, 0xF1BF, 0xE036,
    0x18C1, 0x0948, 0x3BD3, 0x2A5A, 0x5EE5, 0x4F6C, 0x7DF7, 0x6C7E,
    0xA50A, 0xB483, 0x8618, 0x9791, 0xE32E, 0xF2A7, 0xC03C, 0xD1B5,
    0x2942, 0x38CB, 0x0A50, 0x1BD9, 0x6F66, 0x7EEF, 0x4C74, 0x5DFD,
    0xB58B, 0xA402, 0x9699, 0x8710, 0xF3AF, 0xE226, 0xD0BD, 0xC134,
    0x39C3, 0x284A, 0x1AD1, 0x0B58, 0x7FE7, 0x6E6E, 0x5CF5, 0x4D7C,
    0xC60C, 0xD785, 0xE51E, 0xF497, 0x8028, 0x91A1, 0xA33A, 0xB2B3,
    0x4A44, 0x5BCD, 0x6956, 0x78DF, 0x0C60, 0x1DE9, 0x2F72, 0x3EFB,
    0xD68D, 0xC704, 0xF59F, 0xE416, 0x90A9, 0x8120, 0xB3BB, 0xA232,
    0x5AC5, 0x4B4C, 0x79D7, 0x685E, 0x1CE1, 0x0D68, 0x3FF3, 0x2E7A,
    0xE70E, 0xF687, 0xC41C, 0xD595, 0xA12A, 0xB0A3, 0x8238, 0x93B1,
    0x6B46, 0x7ACF, 0x4854, 0x59DD, 0x2D62, 0x3CEB, 0x0E70, 0x1FF9,
    0xF78F, 0xE606, 0xD49D, 0xC514, 0xB1AB, 0xA022, 0x92B9, 0x8330,
    0x7BC7, 0x6A4E, 0x58D5, 0x495C, 0x3DE3, 0x2C6A, 0x1EF1, 0x0F78,
)


def crc16_kermit(data: bytes) -> int:
    """Compute CRC-16/KERMIT (Aqara variant)."""
    crc = 0xFFFF
    for byte in data:
        crc = _CRC16_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return (~crc) & 0xFFFF


def build_control_packet(pkt_type: int, payload_value: int) -> bytes:
    """Build an Aqara LmLocalPacket for TCP control channel."""
    payload_bytes = (
        struct.pack(">B", payload_value & 0xFF)
        if pkt_type == _TYPE_ACK
        else struct.pack(">Q", payload_value)
    )
    header = _MAGIC + struct.pack(">BH", pkt_type, len(payload_bytes))
    crc = crc16_kermit(header[2:] + payload_bytes)
    return header + payload_bytes + struct.pack(">H", crc)


def parse_control_packet(data: bytes) -> dict[str, int] | None:
    """Parse an Aqara LmLocalPacket from raw bytes."""
    if len(data) < 8 or data[0:2] != _MAGIC:
        return None
    pkt_type = data[2]
    if pkt_type > _TYPE_HEARTBEAT:
        return None
    payload_len = struct.unpack(">H", data[3:5])[0]
    if len(data) < 5 + payload_len + 2:
        return None
    expected_crc = struct.unpack(">H", data[5 + payload_len : 7 + payload_len])[0]
    if crc16_kermit(data[2 : 5 + payload_len]) != expected_crc:
        return None
    payload_bytes = data[5 : 5 + payload_len]
    value = (
        payload_bytes[0]
        if pkt_type == _TYPE_ACK
        else int.from_bytes(payload_bytes, "big")
    )
    return {"type": pkt_type, "value": value}


def build_rtp_header(
    payload_type: int, timestamp: int, ssrc: int, seq_num: int
) -> bytes:
    """Build a 12-byte RFC 3550 RTP header."""
    return struct.pack(
        ">BBHII",
        0x80,
        payload_type,
        seq_num & 0xFFFF,
        timestamp & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )


def extract_adts_frames(data: bytes) -> list[bytes]:
    """Extract complete AAC-ADTS frames from a byte buffer."""
    frames: list[bytes] = []
    offset = 0
    while offset < len(data) - 7:
        if data[offset] != 0xFF or (data[offset + 1] & 0xF0) != 0xF0:
            offset += 1
            continue
        frame_len = (
            ((data[offset + 3] & 0x03) << 11)
            | (data[offset + 4] << 3)
            | (data[offset + 5] >> 5)
        )
        if frame_len < 7 or offset + frame_len > len(data):
            break
        frames.append(data[offset : offset + frame_len])
        offset += frame_len
    return frames


class _UDPSenderProtocol(asyncio.DatagramProtocol):
    """Minimal UDP protocol for outgoing RTP packets."""

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Aqara talkback UDP error: %s", exc)


async def async_transcode_to_adts(audio_source: str) -> list[bytes]:
    """Transcode a local audio file or URL to 16kHz mono AAC-ADTS frames via ffmpeg."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        audio_source,
        "-vn",
        "-c:a",
        "aac",
        "-profile:a",
        "aac_low",
        "-b:a",
        "32k",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "adts",
        "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as err:
        raise RuntimeError("ffmpeg non trovato nel sistema per l'invio audio.") from err

    stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        err_msg = stderr_bytes.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(
            f"Errore ffmpeg nella conversione audio: {err_msg or f'code={proc.returncode}'}"
        )
    frames = extract_adts_frames(stdout_bytes)
    if not frames:
        raise RuntimeError("Nessun frame audio AAC valido estratto dalla sorgente.")
    return frames


async def async_play_audio_to_camera(camera_ip: str, audio_source: str) -> int:
    """Open local talkback session (TCP 54324 / UDP 54323) and stream audio frames."""
    frames = await async_transcode_to_adts(audio_source)
    session_ts = int(time.time() * 1000)

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(camera_ip, DEFAULT_TALKBACK_TCP_PORT),
        timeout=4.0,
    )
    udp_transport: asyncio.DatagramTransport | None = None
    hb_task: asyncio.Task[None] | None = None

    try:
        writer.write(build_control_packet(_TYPE_START_VOICE, session_ts))
        await writer.drain()

        ack_raw = await asyncio.wait_for(reader.read(512), timeout=3.0)
        parsed = parse_control_packet(ack_raw) if ack_raw else None
        if not parsed or parsed["type"] != _TYPE_ACK or parsed["value"] != 0:
            raise RuntimeError(f"La camera ha rifiutato il canale vocale locale: {parsed}")

        loop = asyncio.get_running_loop()
        udp_transport, _ = await loop.create_datagram_endpoint(
            _UDPSenderProtocol,
            remote_addr=(camera_ip, DEFAULT_TALKBACK_UDP_PORT),
        )

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                if writer.is_closing():
                    break
                writer.write(build_control_packet(_TYPE_HEARTBEAT, session_ts))
                await writer.drain()

        hb_task = asyncio.create_task(_heartbeat())

        ssrc = random.randint(1, 2_147_483_647)
        ts_samples = 0
        for seq, frame in enumerate(frames):
            header = build_rtp_header(_RTP_PAYLOAD_TYPE, ts_samples, ssrc, seq)
            udp_transport.sendto(header + frame)
            ts_samples += _AAC_SAMPLES_PER_FRAME
            await asyncio.sleep(_AAC_FRAME_INTERVAL)

        return len(frames)
    finally:
        if hb_task is not None:
            hb_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await hb_task
        if udp_transport is not None:
            udp_transport.close()
        if not writer.is_closing():
            with suppress(Exception):
                writer.write(build_control_packet(_TYPE_STOP_VOICE, session_ts))
                await writer.drain()
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
