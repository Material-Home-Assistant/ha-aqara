<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/ha_aqara/brand/dark_logo@2x.png">
    <img alt="HA Aqara" src="custom_components/ha_aqara/brand/logo@2x.png" width="320">
  </picture>
</p>

<h1 align="center">HA Aqara for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/Material-Home-Assistant/ha-aqara/actions/workflows/ci.yaml"><img src="https://github.com/Material-Home-Assistant/ha-aqara/actions/workflows/ci.yaml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Material-Home-Assistant/ha-aqara/releases"><img src="https://img.shields.io/github/v/release/Material-Home-Assistant/ha-aqara.svg" alt="Release"></a>
</p>

Custom integration for **Home Assistant** built for the **Aqara Camera G100 (`lumi.camera.agl005`)**, featuring a built-in **Single Connection RTSP Gatekeeper**, selectable **HLS (TCP) / HA Built-in WebRTC (`go2rtc`)** streaming engine, **100% LAN Talkback audio**, and optional **Aqara Home App synchronization**.

## Table of Contents

1. Before Starting (Aqara Home App Prerequisites)
2. Installation (HACS and Manual)
3. Initial Setup Wizard
4. Architecture: What runs 100% in LAN vs Why the Aqara Login Exists
5. Network Ports and Protocols
6. All Exposed Entities
7. Custom Services and Usage Examples
8. Technical Protocol Details (Privacy, Status LED, Flash, Talkback)
9. Diagnostic Scanner and Troubleshooting

## 1. Before Starting (Aqara Home App Prerequisites)

Before adding the integration in Home Assistant:

1. Download and set up the **Aqara Home** app (iOS / Android) and pair your **Aqara Camera G100** to your local WiFi network.
2. Open the camera settings in the **Aqara Home** app and navigate to **More Settings → LAN RTSP Preview**.
3. Enable **LAN RTSP Preview**, create/note your RTSP **Username**, **Password**, and port (default `8554`).
4. Optional (only if you want AI sensors, Spotlight, Status LED, Night Vision, and App Privacy sync): keep your **Aqara Home email**, **password**, and **account region** (`EU`, `USA`, `CN`, `RU`, or `KR`) ready.

## 2. Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Material-Home-Assistant&repository=ha-aqara)

1. Click the **Open HACS Repository** button above, or open HACS in Home Assistant, go to the top right menu (`⋮`), select **Custom repositories**, paste `https://github.com/Material-Home-Assistant/ha-aqara`, and choose category **Integration**.
2. Click **Download** on **HA Aqara**.
3. Restart **Home Assistant** completely.
4. Go to **Settings → Devices & Services → Add Integration** and search for **HA Aqara**.

### Manual installation

1. Download the latest `ha-aqara.zip` archive from the [GitHub Releases](https://github.com/Material-Home-Assistant/ha-aqara/releases) page (or copy [`custom_components/ha_aqara`](custom_components/ha_aqara) from this repository) into `/config/custom_components/ha_aqara` on your Home Assistant server.
2. Restart **Home Assistant** completely.
3. Go to **Settings → Devices & Services → Add Integration** and search for **HA Aqara**.

## 3. Initial Setup Wizard

1. **Step 1 (Camera Discovery)**: select the camera automatically discovered on your LAN (or choose manual IP entry). Enter RTSP username, RTSP password, RTSP port (default `8554`), default channel (`ch1`, available in 2K only when Apple HomeKit is disabled on the camera), and _Idle Transmission Guard_ preference.
2. **Step 2 (Aqara Home Account Sync, optional)**:
   1. Enter your **Aqara Home Email**, **Password**, and **Account Region** (`EU`, `USA`, `CN`, `RU`, or `KR`).
   2. **Camera Aqara ID** (`lumi3.xxx`) is automatically prefilled from local mDNS UDP `5353` (keep `lumi3.xxx` rather than legacy MAC based `lumi1.xxx` guesses).
   3. Leave Email and Password empty if you want strictly LAN only RTSP + Talkback mode (Aqara cloud controls and AI sensors will stay hidden).
3. Open **Configure** (Options) on the integration card at any time to update IP, RTSP settings, or Aqara credentials.

## 4. Architecture: 100% Local LAN vs Aqara Account Sync

On the **Aqara Camera G100** (`lumi.camera.agl005`, firmware `4.5.45_0004`), the camera only opens TCP ports `8554` (RTSP), `54324` / UDP `54323` (Talkback audio), and two undocumented proprietary binary ports (`10099`, `18641`) on the local network. Both `34318` (Aqara Hub LANLink JSON port) and `443` (local HTTPS) are closed.

In addition, pairing the G100 through **Apple HomeKit** caps the video stream from `2K` (`2304x1296`) down to `1080p` and does not expose the spotlight, AI settings, or advanced camera parameters. For this reason, `ha_aqara` cleanly separates local media transport from optional Aqara cloud controls:

### 100% Local LAN Features (Works WITHOUT an Aqara Account Login)

If you leave Email and Password blank during setup (or clear them later in Options), `ha_aqara` makes **zero outbound cloud connections**, automatically hides and removes incompatible Aqara cloud controls and AI sensors, and exposes strictly the 100% local LAN entities:

1. **Automatic mDNS Discovery (`UDP 5353`) and LAN Scan**: discovers camera IP, hostname, model, firmware version, and the real Aqara device ID (`lumi3.xxx`) directly on your local subnet, with manual IP entry always available as a fallback.
2. **Single Connection RTSP Gatekeeper (`MAX_CAMERA_CONNECTIONS = 1`)**: built-in proxy in [`rtsp_gatekeeper.py`](custom_components/ha_aqara/rtsp_gatekeeper.py) listening on `127.0.0.1` with `TCP_NODELAY` and TCP interleaved RTSP transport, guaranteeing at most **one single open TCP/RTSP connection** to the G100 shared across Home Assistant live streams and snapshots.
3. **Selectable Live Stream Engine (`Auto`, `HLS / Stream TCP`, `WebRTC via HA go2rtc`)**: lets you force TCP HLS (recommended for Docker bridge setups) or use Home Assistant's built-in `go2rtc` WebRTC provider for sub-second latency without external add-ons.
4. **Idle Transmission Guard**: when enabled and no viewer is actively streaming, prevents Home Assistant background dashboard snapshots from waking the camera RTSP encoder.
5. **Local Gatekeeper Privacy Mode & Camera Power Switch**: immediately tears down any active RTSP upstream session and rejects new local stream attempts with `403 Forbidden`.
6. **Local LAN Talkback (`TCP 54324` and `UDP 54323`)**: the `ha_aqara.play_audio` service transcodes audio with `ffmpeg` and streams voice or sound files directly to the camera speaker over your LAN.
7. **Local Telemetry**: connectivity status, open LAN ports, TCP latency, live bitrate, bytes received, active connections, and blocked extra connections.

### Optional Aqara Home Account Sync (When Email & Password Are Provided)

When you provide your Aqara Home credentials (region `EU`, `USA`, `CN`, `RU`, or `KR`), [`aqara_app_client.py`](custom_components/ha_aqara/aqara_app_client.py) communicates with Aqara regional RPC endpoints (`EU`, `USA`, `CN`, `RU`, or `KR`) to unlock hardware controls and AI events unavailable over LAN:

1. **Full App Privacy Sync**: puts the camera into sleep/privacy protection inside the Aqara Home app (`14.125.85`) as well as blocking RTSP locally, and wakes the video feed back up via `14.125.85` and `set_video`.
2. **Hardware Controls**: Status LED, Spotlight / Flash, Night Vision mode, Speaker Volume, Audio Recording mute, Timestamp Overlay, Logo Watermark, Detection Box, 10 Minute Timed Snapshot, Offline Notification, Timelapse Video, Anti Theft Lock, and AI sensitivity/interval tuning.
3. **AI Detections & Hardware Diagnostics**: Motion, Person, Pet, Package, Vehicle, Abnormal Sound, WiFi Signal Strength (RSSI), and `CameraActiveStatus`.

## 5. Network Ports and Protocols

<table>
  <thead>
    <tr>
      <th>Port</th>
      <th>Protocol</th>
      <th>Direction</th>
      <th>Purpose</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>8554</code></td>
      <td>TCP (RTSP / RTP interleaved)</td>
      <td>HA Gatekeeper to G100</td>
      <td>2K High (`ch1`), Medium (`ch2`), and Low (`ch3`) RTSP streams</td>
    </tr>
    <tr>
      <td><code>54324</code></td>
      <td>TCP (`LmLocalPacket` CRC16 KERMIT)</td>
      <td>HA to G100</td>
      <td>Local Talkback control session (`START`, `HEARTBEAT`, `STOP`)</td>
    </tr>
    <tr>
      <td><code>54323</code></td>
      <td>UDP (RTP PT 97, AAC ADTS 16kHz mono)</td>
      <td>HA to G100</td>
      <td>Local audio RTP packets sent directly to the G100 speaker</td>
    </tr>
    <tr>
      <td><code>5353</code></td>
      <td>UDP Multicast / Unicast (mDNS)</td>
      <td>HA and G100 on LAN</td>
      <td>Discovery of `_aqara._tcp.local.` and extraction of real ID `lumi3.xxx`</td>
    </tr>
    <tr>
      <td><code>443</code></td>
      <td>HTTPS (optional, only with Aqara login)</td>
      <td>HA to Aqara Regional Server</td>
      <td>Hardware RID writes, App Privacy sync, and V3 AI Trait polling</td>
    </tr>
  </tbody>
</table>

## 6. All Exposed Entities

### Camera (`camera`)

**Primary Camera Entity (`Aqara G100`)**: serves the selected RTSP stream (`ch1` `2304x1296`, `ch2` `640x360`, or `ch3`) through the single connection gatekeeper, using either HA HLS (`stream`) or HA built-in WebRTC (`go2rtc`) according to `Live Stream Engine`. Extra state attributes expose `active_channel`, `stream_engine`, `webrtc_provider_available`, `privacy_mode`, `idle_transmission_guard`, `active_connections`, `max_connections` (`1`), `blocked_connections`, `bitrate_kbps`, and `gatekeeper_port`.

### Switches (`switch`)

<table>
  <thead>
    <tr>
      <th>Entity</th>
      <th>Category</th>
      <th>Resource / RID</th>
      <th>Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>Camera Power</code></td>
      <td>Primary</td>
      <td>Gatekeeper + <code>14.125.85</code> / <code>set_video</code></td>
      <td>Intuitive power toggle (`ON` disables Privacy Mode and wakes video; `OFF` enables Privacy Mode without rebooting)</td>
    </tr>
    <tr>
      <td><code>Privacy Mode</code></td>
      <td>Primary</td>
      <td>Gatekeeper + <code>14.125.85</code> / <code>set_video</code></td>
      <td>Cuts local RTSP stream immediately and toggles Privacy / Video in Aqara App</td>
    </tr>
    <tr>
      <td><code>Spotlight / Flash</code></td>
      <td>Primary</td>
      <td><code>14.158.85</code></td>
      <td>Turns the built in front LED spotlight on or off</td>
    </tr>
    <tr>
      <td><code>Idle Transmission Guard</code></td>
      <td>Config</td>
      <td>Local HA</td>
      <td>Suppresses periodic dashboard stills while idle so stream stays dormant</td>
    </tr>
    <tr>
      <td><code>Status LED</code></td>
      <td>Config</td>
      <td><code>14.21.85</code> / <code>8.0.2032</code></td>
      <td>Turns the camera indicator status LED on or off</td>
    </tr>
    <tr>
      <td><code>Motion Detection</code></td>
      <td>Config</td>
      <td><code>14.70.85</code></td>
      <td>Enables or disables hardware motion detection</td>
    </tr>
    <tr>
      <td><code>Person Detection</code></td>
      <td>Config</td>
      <td><code>14.77.85</code></td>
      <td>Enables or disables AI human/person detection</td>
    </tr>
    <tr>
      <td><code>Animal Detection</code></td>
      <td>Config</td>
      <td><code>14.78.85</code></td>
      <td>Enables or disables AI cat/dog detection</td>
    </tr>
    <tr>
      <td><code>Face Detection</code></td>
      <td>Config</td>
      <td><code>14.75.85</code></td>
      <td>Enables or disables face recognition</td>
    </tr>
    <tr>
      <td><code>Vehicle Detection</code></td>
      <td>Config</td>
      <td><code>14.159.85</code></td>
      <td>Enables or disables AI vehicle detection</td>
    </tr>
    <tr>
      <td><code>Package Detection</code></td>
      <td>Config</td>
      <td><code>4.94.85</code></td>
      <td>Enables or disables AI package detection</td>
    </tr>
    <tr>
      <td><code>Fire Detection</code></td>
      <td>Config</td>
      <td><code>4.193.85</code></td>
      <td>Enables or disables AI fire/flame detection</td>
    </tr>
    <tr>
      <td><code>Disable Audio Recording</code></td>
      <td>Config</td>
      <td><code>4.63.85</code></td>
      <td>Mutes camera microphone recording</td>
    </tr>
    <tr>
      <td><code>Timestamp Overlay</code></td>
      <td>Config</td>
      <td><code>14.4.85</code></td>
      <td>Shows or hides timestamp OSD on video frames</td>
    </tr>
    <tr>
      <td><code>Logo Watermark</code></td>
      <td>Config</td>
      <td><code>4.60.85</code></td>
      <td>Shows or hides the Aqara watermark on video</td>
    </tr>
    <tr>
      <td><code>Detection Box</code></td>
      <td>Config</td>
      <td><code>4.76.85</code></td>
      <td>Draws bounding boxes around detected objects in video</td>
    </tr>
    <tr>
      <td><code>10 Minute Timed Snapshot</code></td>
      <td>Config</td>
      <td><code>4.187.85</code></td>
      <td>Captures periodic timeline snapshots every 10 minutes</td>
    </tr>
    <tr>
      <td><code>Device Offline Notification</code></td>
      <td>Config</td>
      <td><code>4.89.85</code></td>
      <td>Enables push alerts if the camera disconnects</td>
    </tr>
    <tr>
      <td><code>Timelapse Video</code></td>
      <td>Config</td>
      <td><code>4.27.85</code></td>
      <td>Enables or disables timelapse recording</td>
    </tr>
    <tr>
      <td><code>Anti Theft Lock</code></td>
      <td>Config</td>
      <td><code>4.99.85</code></td>
      <td>Locks the device against unauthorized rebinding</td>
    </tr>
  </tbody>
</table>

### Binary Sensors (`binary_sensor`)

All AI and motion event binary sensors hold their active state for `30 seconds` (`MOTION_HOLD_SECONDS`) upon each newly observed trigger edge.

<table>
  <thead>
    <tr>
      <th>Entity</th>
      <th>Category</th>
      <th>Source</th>
      <th>Notes</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>Motion</code></td>
      <td>Primary</td>
      <td>Trait <code>5.160.33000</code> and legacy RIDs</td>
      <td>Also triggers whenever Person, Pet, Package, or Vehicle fires</td>
    </tr>
    <tr>
      <td><code>Person Detected</code></td>
      <td>Primary</td>
      <td>V3 Trait <code>3.216.20215</code></td>
      <td>AI human detection event</td>
    </tr>
    <tr>
      <td><code>Pet Detected</code></td>
      <td>Primary</td>
      <td>V3 Trait <code>6.218.20216</code></td>
      <td>AI cat or dog detection event</td>
    </tr>
    <tr>
      <td><code>Package Detected</code></td>
      <td>Primary</td>
      <td>V3 Trait <code>8.223.20221</code></td>
      <td>AI package detection event</td>
    </tr>
    <tr>
      <td><code>Vehicle Detected</code></td>
      <td>Primary</td>
      <td>V3 Trait <code>9.221.20219</code></td>
      <td>AI vehicle detection event</td>
    </tr>
    <tr>
      <td><code>Sound Detected</code></td>
      <td>Primary</td>
      <td>RID <code>13.2.85</code> / <code>3.3.85</code></td>
      <td>Abnormal sound detection event</td>
    </tr>
    <tr>
      <td><code>Online</code></td>
      <td>Diagnostic</td>
      <td>Local TCP probe</td>
      <td>True when port <code>8554</code> or <code>54324</code> responds on LAN</td>
    </tr>
    <tr>
      <td><code>Actively Transmitting</code></td>
      <td>Primary</td>
      <td>Local Gatekeeper</td>
      <td>True while real RTSP media flows (&ge; 8192 bytes or RTP <code>$</code> packets)</td>
    </tr>
    <tr>
      <td><code>Camera Hardware Active</code></td>
      <td>Diagnostic</td>
      <td>V3 Trait <code>2.136.32930</code></td>
      <td>Firmware active state (`1` awake, `0` asleep or in privacy)</td>
    </tr>
    <tr>
      <td><code>RTSP Port (8554)</code></td>
      <td>Diagnostic</td>
      <td>TCP Probe <code>8554</code></td>
      <td>Checks local RTSP server reachability</td>
    </tr>
    <tr>
      <td><code>Local Audio P2P Port (54324)</code></td>
      <td>Diagnostic</td>
      <td>TCP Probe <code>54324</code></td>
      <td>Checks local Talkback channel availability</td>
    </tr>
    <tr>
      <td><code>Single Connection Gatekeeper Active</code></td>
      <td>Diagnostic</td>
      <td>Local server <code>127.0.0.1</code></td>
      <td>Status of the internal single connection proxy</td>
    </tr>
    <tr>
      <td><code>Aqara App Sync Connected</code></td>
      <td>Diagnostic</td>
      <td>Aqara RPC client</td>
      <td>Indicates healthy sync with Aqara regional servers</td>
    </tr>
  </tbody>
</table>

### Sensors (`sensor`)

<table>
  <thead>
    <tr>
      <th>Entity</th>
      <th>Category</th>
      <th>Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>Stream Status</code></td>
      <td>Primary</td>
      <td>Unified stream state: <code>Live Streaming</code>, <code>Idle Standby</code>, <code>Privacy Blocked</code>, or <code>Offline</code></td>
    </tr>
    <tr>
      <td><code>Last Motion Event</code></td>
      <td>Primary</td>
      <td>Formatted in your local Home Assistant timezone as <code>YYYY-MM-DD HH:MM:SS</code> without <code>T</code>, microseconds, or UTC offset</td>
    </tr>
    <tr>
      <td><code>Active Camera Connections</code></td>
      <td>Diagnostic</td>
      <td>Open RTSP connections to the camera (<code>0</code> or <code>1</code>, hard limit <code>1</code>)</td>
    </tr>
    <tr>
      <td><code>Live Bitrate</code></td>
      <td>Diagnostic</td>
      <td>Real time RTSP throughput in <code>kbps</code></td>
    </tr>
    <tr>
      <td><code>Network Latency</code></td>
      <td>Diagnostic</td>
      <td>Local LAN TCP connect latency in `ms`</td>
    </tr>
    <tr>
      <td><code>Stream Data Received</code></td>
      <td>Diagnostic</td>
      <td>Cumulative RTSP stream data transferred in `MB`</td>
    </tr>
    <tr>
      <td><code>Blocked Extra Connections</code></td>
      <td>Diagnostic</td>
      <td>Count of extra simultaneous stream attempts blocked by the gatekeeper</td>
    </tr>
    <tr>
      <td><code>WiFi Signal Strength</code></td>
      <td>Diagnostic</td>
      <td>Reported WiFi RSSI in `dBm` from V3 Trait `1.171.20119`</td>
    </tr>
  </tbody>
</table>

### Selectors (`select`), Numbers (`number`), and Buttons (`button`)

1. **Configuration Selectors (`select`)**:
   1. `Stream Channel`: `ch1` (2K High `2304x1296`, available only when Apple HomeKit is disabled on the camera; with HomeKit enabled the G100 drops to `1080p`), `ch2` (Medium `640x360`), `ch3` (Low)
   2. `Live Stream Engine`: `Auto (HA Default)`, `HLS / Stream (TCP)`, or `WebRTC (HA Built-in go2rtc)`
   3. `Night Vision` (`14.73.85`): `Auto` (`2`), `Always On (IR)` (`1`), `Always Off` (`0`), `Spotlight Color` (`3`)
   4. `Motion Sensitivity` (`14.104.85`): `Low`, `Medium`, `High`
   5. `Person Sensitivity` (`14.136.85`): `Low`, `Medium`, `High`
   6. `Power Frequency` (`14.120.85`): `50 Hz`, `60 Hz`
   7. `Timestamp Format` (`14.134.85`): `YYYY-MM-DD`, `MM-DD-YYYY`, `DD-MM-YYYY`
   8. `Camera Voice Language` (`14.10.85`): 14 languages including `English` and `Italian`
2. **Configuration Numbers (`number`)**:
   1. `Speaker Volume` (`14.11.85`): `0` to `100` `%`
   2. `Motion Detection Interval` (`14.105.85`): `15` to `600` seconds
   3. `Person Detection Interval` (`14.114.85`): `15` to `600` seconds
3. **Action Buttons (`button`)**:
   1. `Force Stop Stream`: immediately disconnects HA stream workers and closes the active gatekeeper socket
   2. `Refresh Local Status` (Diagnostic): triggers an immediate coordinator poll
   3. `Run G100 Diagnostic Scan` (Diagnostic): runs local mDNS (`UDP 5353`), TCP port scan, numeric RID read, named option read (`set_video`, `device_night_tip_light`), App detail view dump, and V3 Trait query, posting the full report as a Home Assistant persistent notification

## 7. Custom Services and Usage Examples

Five services are registered under the `ha_aqara` domain in [`services.yaml`](custom_components/ha_aqara/services.yaml):

1. `ha_aqara.set_privacy_mode`: enables (`enabled: true`) or disables (`enabled: false`) Privacy Mode across both the local gatekeeper and Aqara App.
2. `ha_aqara.stop_transmission`: forcibly closes any active RTSP session on the gatekeeper.
3. `ha_aqara.switch_channel`: dynamically switches active RTSP channel (`ch1`, `ch2`, or `ch3`).
4. `ha_aqara.run_diagnostic_scan`: runs the full technical scan and creates a persistent notification.
5. `ha_aqara.play_audio`: transcodes any local file path (e.g. `/media/chime.mp3`) or HTTP/HTTPS audio URL to `AAC LC 16kHz mono 32kbps ADTS` using `ffmpeg` and streams it over your LAN directly to the camera speaker (`TCP 54324` + `UDP 54323`).

### Example Service Call (Developer Tools → Actions)

```yaml
action: ha_aqara.play_audio
data:
  entity_id: camera.ha_aqara_192_168_1_107
  audio_source: /media/doorbell.mp3
```

## 8. Technical Protocol Details (Privacy, Status LED, Flash, Talkback)

1. **Privacy Mode & Fast Parallel Wakeup**:
   Strictly writes only verified G100 privacy/video controls (`14.125.85` and `set_video` across `/app/v1.0/lumi/res/write` and `/app/v1.0/lumi/app/view/write`) without touching legacy reboot/reset RIDs. Waking live video sends `14.125.85 = "0"` and `set_video = 1` concurrently and verifies hardware trait `2.136.32930` (`CameraActiveStatus`). If waking fails or `CameraActiveStatus` remains `0`, Home Assistant safely reverts `privacy_mode` so the camera card does not retry RTSP every 10 seconds against a sleeping camera.
2. **Status LED (`14.21.85` + fallback `8.0.2032` / `device_night_tip_light`)**:
   Resource `14.21.85` ("Turn off indicator light period") uses a packed integer bitfield `(start_min << 12) | (end_min << 1) | disabled_bit0`, where `bit0 = 0` enables the LED off schedule and `bit0 = 1` keeps the LED enabled (`2878`/`2879` for `00:00..23:59`, or flipping bit 0 on existing schedules such as `5163481` / `5163480`).
3. **Local LAN Talkback ([`talkback.py`](custom_components/ha_aqara/talkback.py))**:
   Opens TCP control channel on port `54324` using `LmLocalPacket` frames (`0xFE 0xEF | Type | Length | Timestamp uint64BE | CRC16 KERMIT`) and sends `1024` sample AAC ADTS audio frames wrapped in `RFC 3550` RTP headers (`PT = 97`) every `64ms` to UDP `54323` with a 5 second TCP heartbeat and clean `STOP_VOICE` shutdown.

## 9. Diagnostic Scanner and Troubleshooting

1. **Aqara App commands return `code=302` or fail to apply**:
   Check Options to confirm `Camera Aqara ID` uses the mDNS ID (`lumi3.xxx`) rather than a legacy MAC ID (`lumi1.xxx`), and press **Run G100 Diagnostic Scan** in Home Assistant to inspect mDNS TXT flags, open ports, RID values, and V3 traits.
2. **Video drops, stalls at `0:00`, or another viewer disconnects Home Assistant**:
   Close external direct RTSP consumers pointing at `camera_ip:8554`, and if running Home Assistant in Docker bridge mode, switch **Live Stream Engine** to `HLS / Stream (TCP)` to bypass WebRTC UDP ICE negotiation.
3. **`ha_aqara.play_audio` reports `ffmpeg` not found**:
   Ensure `ffmpeg` is available on the Home Assistant host PATH (bundled by default in Home Assistant OS and official Container images) and verify `Local Audio P2P Port (54324)` is open.

## Need Help?

[Issues and feature requests](https://github.com/Material-Home-Assistant/ha-aqara/issues) · [GitHub Discussions](https://github.com/Material-Home-Assistant/ha-aqara/discussions)

## Missing translation

Want to see your language? Open an issue or submit a PR with new entries under [`custom_components/ha_aqara/translations/`](custom_components/ha_aqara/translations/).
