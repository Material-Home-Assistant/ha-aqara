"""Constants for the Aqara Camera G100 (100% Local + Aqara App Sync) integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ha_aqara"
MANUFACTURER = "Aqara (Lumi United)"
MODEL_NAME = "Camera G100"
MODEL_ID = "lumi.camera.agl005"

PLATFORMS: list[Platform] = [
    Platform.CAMERA,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.BUTTON,
]

# Local ports exposed by Aqara G100
DEFAULT_RTSP_PORT = 8554
DEFAULT_TALKBACK_TCP_PORT = 54324
DEFAULT_TALKBACK_UDP_PORT = 54323

# Built-in Single-Connection Gatekeeper limits
MAX_CAMERA_CONNECTIONS = 1
DEFAULT_CHANNEL = "ch1"
DEFAULT_SCAN_INTERVAL_SECONDS = 10
DEFAULT_IDLE_SLEEP_SNAPSHOT = True
MOTION_HOLD_SECONDS = 30

# Active LAN discovery settings
DISCOVERY_TIMEOUT = 0.65
DISCOVERY_CONCURRENCY = 64
MANUAL_IP_OPTION = "__manual__"

# RTSP Channels on Aqara G100
CHANNEL_OPTIONS: dict[str, str] = {
    "ch1": "ch1 - 2K High (2304x1296)",
    "ch2": "ch2 - Medium (640x360)",
    "ch3": "ch3 - Low",
}

# Frontend stream engine options
CONF_STREAM_ENGINE = "stream_engine"
STREAM_ENGINE_AUTO = "auto"
STREAM_ENGINE_HLS = "hls"
STREAM_ENGINE_WEBRTC = "webrtc"
STREAM_ENGINE_OPTIONS: tuple[str, ...] = (
    STREAM_ENGINE_AUTO,
    STREAM_ENGINE_HLS,
    STREAM_ENGINE_WEBRTC,
)
DEFAULT_STREAM_ENGINE = STREAM_ENGINE_AUTO

# Config & Option keys
CONF_CAMERA_IP = "camera_ip"
CONF_CAMERA_NAME = "camera_name"
CONF_SELECTED_HOST = "selected_host"
CONF_RTSP_USERNAME = "rtsp_username"
CONF_RTSP_PASSWORD = "rtsp_password"
CONF_RTSP_PORT = "rtsp_port"
CONF_DEFAULT_CHANNEL = "default_channel"
CONF_IDLE_SLEEP_SNAPSHOT = "idle_sleep_snapshot"

# Aqara Home App account sync keys
CONF_AQARA_EMAIL = "aqara_email"
CONF_AQARA_PASSWORD = "aqara_password"
CONF_AQARA_REGION = "aqara_region"
CONF_AQARA_DEVICE_ID = "aqara_device_id"
CONF_AQARA_TOKEN = "aqara_token"
CONF_AQARA_USER_ID = "aqara_user_id"

DEFAULT_AQARA_REGION = "EU"
AQARA_REGIONS: dict[str, dict[str, str]] = {
    "EU": {
        "name": "Europa (EU)",
        "server": "https://rpc-ger.aqara.com",
        "appid": "7be1984f0556276133336839",
        "appkey": "Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    },
    "USA": {
        "name": "Stati Uniti / Globale (USA)",
        "server": "https://aiot-rpc-usa.aqara.com",
        "appid": "7be1984f0556276133336839",
        "appkey": "Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    },
    "CN": {
        "name": "Cina (CN)",
        "server": "https://aiot-rpc.aqara.cn",
        "appid": "94549908487478b220992a70",
        "appkey": "Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    },
    "RU": {
        "name": "Russia (RU)",
        "server": "https://rpc-ru.aqara.com",
        "appid": "94549908487478b220992a70",
        "appkey": "euGhPe2rcmxwculATNj45eEtnd50zp0I",
    },
    "KR": {
        "name": "Corea (KR)",
        "server": "https://rpc-kr.aqara.com",
        "appid": "94549908487478b220992a70",
        "appkey": "euGhPe2rcmxwculATNj45eEtnd50zp0I",
    },
}

# Fallback signing credentials if a region rejects with code 106 (invalid sign)
AQARA_SIGNING_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("7be1984f0556276133336839", "Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3"),
    ("94549908487478b220992a70", "euGhPe2rcmxwculATNj45eEtnd50zp0I"),
    ("94549908487478b220992a70", "Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3"),
)

# Verified G100 (lumi.camera.agl005) resource IDs & legacy camera fallbacks
# NOTE: 14.70.85 on G100 is Motion Detection Enable (NOT sleep/privacy!).
RID_PRIVACY_SET_VIDEO = "set_video"
RID_PRIVACY_VIDEO_SWITCH_LIST: tuple[str, ...] = ("14.74.85", "14.15.85")
RID_CAMERA_SLEEP_LIST: tuple[str, ...] = (
    "14.125.85",  # G100 Privacy / Timed sleep switch ("1"=sleep, "0"=wake)
    "14.1.85",
    "4.1.85",
    "8.0.2062",
)
RID_STATUS_LED_PACKED_PERIOD = "14.21.85"  # "Turn off indicator light period" on G100
RID_LED_NIGHT_TIP = "device_night_tip_light"
RID_SPOTLIGHT = "14.158.85"  # Built-in G100 LED spotlight / flash
RID_STATUS_LED_LIST: tuple[str, ...] = (
    "8.0.2032",
    "14.5.85",
    "8.0.2008",
)
RID_MOTION_LIST: tuple[str, ...] = (
    "3.1.85",
    "13.1.85",
    "3.51.85",
)
RID_PERSON_LIST: tuple[str, ...] = (
    "13.27.85",
    "13.11.85",
    "3.2.85",
)
RID_SOUND_LIST: tuple[str, ...] = (
    "13.2.85",
    "3.3.85",
)

# Verified G100 (lumi.camera.agl005) V3 qlink trait paths
TRAIT_G100_PERSON = "3.216.20215"      # HumanRecognition.HumanRecognitionReport
TRAIT_G100_OCCUPANCY = "5.160.33000"   # OccupancySensing.Occupancy (Motion)
TRAIT_G100_PET = "6.218.20216"         # PetRecognition.PetRecognitionReport
TRAIT_G100_PACKAGE = "8.223.20221"     # PackageRecognition.PackageRecognitionReport
TRAIT_G100_VEHICLE = "9.221.20219"     # VehicleDetection.VehicleDetected
TRAIT_G100_WIFI_RSSI = "1.171.20119"   # NetworkCommissioning.WiFiRSSI
TRAIT_G100_ACTIVE = "2.136.32930"      # Camera.CameraActiveStatus
TRAIT_G100_P2P_ENABLED = "2.136.20030" # Camera.P2PCaptureEnabled
TRAIT_G100_P2P_STATUS = "2.136.20036"  # Camera.P2PCaptureStatus
TRAIT_G100_RTSP_URL = "7.136.20368"    # Camera.CameraRTSPURL
TRAIT_G100_POWER_MODE = "1.147.33089"  # PowerSource.PowerMode

G100_POLL_TRAITS: tuple[str, ...] = (
    TRAIT_G100_OCCUPANCY,
    TRAIT_G100_PERSON,
    TRAIT_G100_PET,
    TRAIT_G100_PACKAGE,
    TRAIT_G100_VEHICLE,
    TRAIT_G100_WIFI_RSSI,
    TRAIT_G100_ACTIVE,
)

G100_DIAGNOSTIC_TRAITS: tuple[str, ...] = G100_POLL_TRAITS + (
    TRAIT_G100_P2P_ENABLED,
    TRAIT_G100_P2P_STATUS,
    TRAIT_G100_RTSP_URL,
    TRAIT_G100_POWER_MODE,
)

G100_NAMED_DIAGNOSTIC_OPTIONS: tuple[tuple[str, str], ...] = (
    ("set_video", "Camera Video Active (set_video)"),
    ("device_night_tip_light", "Turn Off Indicator Light (device_night_tip_light)"),
    ("time_sleep_enable", "Timed Sleep Switch (time_sleep_enable)"),
)

G100_DIAGNOSTIC_RIDS: tuple[tuple[str, str], ...] = (
    ("14.125.85", "Privacy / Timed Sleep Mode"),
    ("14.74.85", "Privacy Protection (set_video RID)"),
    ("14.15.85", "Privacy Protection Alt"),
    ("14.38.85", "Sleep Schedule"),
    ("14.1.85", "Sleep / Power Alt"),
    ("4.1.85", "Power Switch Alt"),
    ("8.0.2062", "Sleep Alt"),
    ("14.21.85", "Turn Off Indicator Light Period (LED)"),
    ("8.0.2032", "Status LED (Legacy)"),
    ("14.5.85", "Status LED Alt"),
    ("8.0.2008", "Status LED Legacy Alt"),
    ("14.70.85", "Motion Detection Enable"),
    ("14.77.85", "Person Detection Enable"),
    ("14.78.85", "Animal Detection Enable"),
    ("14.75.85", "Face Detection Enable"),
    ("14.159.85", "Vehicle Detection Enable"),
    ("4.94.85", "Package Detection Enable"),
    ("4.193.85", "Fire Detection Enable"),
    ("14.104.85", "Motion Detection Sensitivity"),
    ("14.136.85", "Person Detection Sensitivity"),
    ("14.105.85", "Motion Detection Time Interval"),
    ("14.114.85", "Person Detection Time Interval"),
    ("14.158.85", "Spotlight"),
    ("14.73.85", "Night Vision"),
    ("14.11.85", "Device Volume"),
    ("14.10.85", "Device Language"),
    ("14.120.85", "Power Frequency"),
    ("14.134.85", "Timestamp Format"),
    ("14.4.85", "Timestamp Overlay"),
    ("4.60.85", "Logo Watermark"),
    ("4.76.85", "Detection Box"),
    ("4.63.85", "Disable Audio Recording"),
    ("4.187.85", "10-Minute Timed Snapshot"),
    ("4.89.85", "Device Offline Notification"),
    ("4.27.85", "Timelapse Video"),
    ("4.99.85", "Anti-Theft Lock"),
    ("3.1.85", "Motion Event Legacy"),
    ("13.1.85", "Motion Trigger Legacy"),
    ("13.2.85", "Sound Event Legacy"),
)

# Writable G100 (lumi.camera.agl005) config RIDs polled every cycle
G100_POLL_CONFIG_RIDS: tuple[str, ...] = (
    "14.70.85",
    "14.77.85",
    "14.78.85",
    "14.75.85",
    "14.159.85",
    "4.94.85",
    "4.193.85",
    "4.63.85",
    "14.4.85",
    "4.60.85",
    "4.76.85",
    "4.187.85",
    "4.89.85",
    "4.27.85",
    "4.99.85",
    "14.73.85",
    "14.104.85",
    "14.136.85",
    "14.120.85",
    "14.134.85",
    "14.10.85",
    "14.11.85",
    "14.105.85",
    "14.114.85",
)

# Wire value -> translation option slug mappings for G100 Select entities
NIGHT_VISION_OPTIONS: dict[str, str] = {
    "2": "auto",
    "1": "always_on",
    "0": "always_off",
    "3": "spotlight",
}
SENSITIVITY_OPTIONS: dict[str, str] = {
    "0": "low",
    "1": "medium",
    "2": "high",
}
POWER_FREQUENCY_OPTIONS: dict[str, str] = {
    "0": "50hz",
    "1": "60hz",
}
TIMESTAMP_FORMAT_OPTIONS: dict[str, str] = {
    "%Y-%m-%d %H:%M:%S": "ymd",
    "%m-%d-%Y %H:%M:%S": "mdy",
    "%d-%m-%Y %H:%M:%S": "dmy",
}
DEVICE_LANGUAGE_OPTIONS: dict[str, str] = {
    "0": "default",
    "1": "chinese",
    "2": "english",
    "3": "cantonese",
    "4": "spanish",
    "5": "russian",
    "6": "korean",
    "7": "portuguese",
    "8": "german",
    "9": "french",
    "10": "italian",
    "11": "japanese",
    "12": "polish",
    "13": "arabic",
}

# Stream states
STREAM_STATE_PRIVACY = "privacy_active"
STREAM_STATE_STREAMING = "streaming"
STREAM_STATE_IDLE = "idle_standby"
STREAM_STATE_OFFLINE = "offline"

# Custom services
SERVICE_SET_PRIVACY_MODE = "set_privacy_mode"
SERVICE_STOP_TRANSMISSION = "stop_transmission"
SERVICE_SWITCH_CHANNEL = "switch_channel"
SERVICE_RUN_DIAGNOSTIC_SCAN = "run_diagnostic_scan"
SERVICE_PLAY_AUDIO = "play_audio"

ATTR_ENABLED = "enabled"
ATTR_CHANNEL = "channel"
ATTR_ENTITY_ID = "entity_id"
ATTR_AUDIO_SOURCE = "audio_source"
