# ha-snapmaker-u1

> A Home Assistant custom integration for the **Snapmaker U1** 3D printer,
> inspired by [ha-bambulab](https://github.com/greghesp/ha-bambulab).

## How it works

The Snapmaker U1 runs a modified **Klipper** firmware with a modified **Moonraker**
API server. This integration connects directly to that API over your local network
using:

- **WebSocket** (JSON-RPC 2.0) at `ws://<printer-ip>/websocket` for real-time
  push updates (no polling delay).
- **HTTP REST** at `http://<printer-ip>/` as a fallback and for sending commands.

No Snapmaker account or cloud connection is required.

---

## Features

### Sensors
| Entity | Description |
|---|---|
| Print State | `standby` / `printing` / `paused` / `complete` / `error` / `cancelled` |
| Print Progress | Percentage complete |
| Current Layer | Current layer number |
| Total Layers | Total layers in the print |
| Print Duration | Active printing time (excludes pauses) |
| Time Remaining | Estimated seconds remaining |
| Filament Used | Millimetres of filament consumed |
| Filename | Name of the file being printed |
| Bed Temperature | Current heated-bed temperature |
| Bed Target | Heated-bed target temperature |
| T0–T3 Nozzle Temperature | Per-extruder current temperature |
| T0–T3 Nozzle Target | Per-extruder target temperature |
| Part Cooling Fan | Fan speed as a percentage |
| Klipper State | `ready` / `startup` / `shutdown` / `error` |
| Toolhead Position | X/Y/Z coordinates (with individual attributes) |

### Binary Sensors
| Entity | Description |
|---|---|
| Printer Ready | `true` when Klipper reports `ready` |
| Printing | `true` while a print is actively running |
| Paused | `true` while a print is paused |
| Error | `true` when a Klipper/print error is active |

### Buttons
| Button | Action |
|---|---|
| Pause Print | Pauses the active print |
| Resume Print | Resumes a paused print |
| Cancel Print | Cancels the active print |
| Emergency Stop | Immediately halts all motion |
| Home All Axes | `G28` |
| Home X / Y / Z | Home individual axis |
| Restart Klipper | Restarts the Klipper process |

### Camera
- **Webcam** – MJPEG stream from `/webcam/stream.mjpg`
  Snapshot images pulled from `/webcam/snapshot.jpg`

### Services
| Service | Description |
|---|---|
| `snapmaker_u1.execute_gcode` | Send any G-code command or macro |
| `snapmaker_u1.set_bed_temperature` | Set heated-bed target (°C) |
| `snapmaker_u1.set_nozzle_temperature` | Set a nozzle target (°C) by extruder index |

---

## Installation

### HACS (recommended)
1. In HACS → **Integrations** → three-dot menu → **Custom repositories**
2. Add this repository URL, category **Integration**
3. Search for "Snapmaker U1" and install
4. Restart Home Assistant

### Manual
1. Copy the `custom_components/snapmaker_u1` folder into your HA
   `config/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Snapmaker U1**
3. Enter:
   - **IP Address** – the local IP of your printer (e.g. `192.168.1.42`)
   - **Port** – `80` by default (Moonraker is proxied through nginx)
   - **API Key** – leave blank unless you have configured Moonraker authentication

The integration will test the connection before saving. If successful, all
entities are created immediately.

---

## Authentication

The stock U1 firmware configures Moonraker with trusted LAN clients, so **no
API key is required** if Home Assistant is on the same network as the printer.

If you use the
[Extended Firmware](https://github.com/paxx12/SnapmakerU1-Extended-Firmware)
with forced logins enabled, generate an API key in Moonraker and paste it into
the API Key field.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot connect" during setup | Verify the IP, confirm the printer is on and that Fluidd loads in your browser at that address |
| Sensors stuck at `unavailable` | Check Klipper logs via Fluidd; the WebSocket may be reconnecting |
| Camera shows no image | Ensure the webcam is enabled in Moonraker's config; test `http://<ip>/webcam/snapshot.jpg` directly |
| Temperatures show 0 after startup | Klipper may still be initialising; state will update within seconds once ready |

---

## Technical notes

- **Protocol**: Moonraker WebSocket (JSON-RPC 2.0) + HTTP REST
- **IoT class**: `local_push` (push updates via WebSocket, no cloud dependency)
- **Reconnection**: Exponential back-off from 5 s to 60 s
- **Extruders**: Up to 4 nozzles auto-detected (T0–T3)
- **HA version**: Tested against Home Assistant 2024.1+

---

## Credits

Architecture inspired by [ha-bambulab](https://github.com/greghesp/ha-bambulab)
by Greg Hesp and Adrian Garside.

Moonraker API documentation: https://moonraker.readthedocs.io
