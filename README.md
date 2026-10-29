<img src="brand/icon@2x.png" alt="Snapmaker U1 integration icon" width="112" align="right">

# ha-snapmaker-u1

> A Home Assistant custom integration for the **Snapmaker U1** 3D printer,
> inspired by [ha-bambulab](https://github.com/greghesp/ha-bambulab).
> Live status, temperatures, loaded filament colours, camera, print controls,
> BIQU Panda Breath support and a built-in dashboard card. No cloud needed.

![Snapmaker U1 dashboard card: printing (light and dark), paused and idle](docs/images/card-overview.png)

<sub>Mockups of the bundled dashboard card rendered with sample data. Your card
shows your printer's real camera and values.</sub>

## Contents

- [How it works](#how-it-works)
- [Features](#features)
- [Dashboard card](#dashboard-card)
- [Filament type and colour](#filament-type-and-colour)
- [Panda Breath / DragonBreath chamber heater](#panda-breath--dragonbreath-chamber-heater)
- [Installation](#installation)
- [Configuration](#configuration)
- [Authentication](#authentication)
- [Automations](#automations)
- [Troubleshooting](#troubleshooting)
- [Technical notes](#technical-notes)

---

## How it works

The Snapmaker U1 runs a modified **Klipper** firmware with a modified **Moonraker**
API server. This integration connects directly to that API over your local network
using:

- **WebSocket** (JSON-RPC 2.0) at `ws://<printer-ip>/websocket` for real-time
  push updates (no polling delay).
- **HTTP REST** at `http://<printer-ip>/` as a fallback and for sending commands.

No Snapmaker account or cloud connection is required. The integration does
**not** include its own Moonraker; it talks to the one running on the printer.

---

## Features

### Sensors
| Entity | Description |
|---|---|
| Print State | `standby` / `printing` / `paused` / `complete` / `error` / `cancelled` |
| Print Progress | Percentage complete |
| Current Layer / Total Layers | Layer counter for the current print |
| Print Duration | Active printing time (excludes pauses) |
| Total Duration | Time since the print started (includes pauses) |
| Time Remaining | Estimated time left |
| Print ETA | Estimated finish time (timestamp) |
| Filament Used | Millimetres of filament consumed |
| Filename | Name of the file being printed |
| Bed Temperature / Bed Target | Heated-bed current and target temperature |
| T0–T3 Nozzle Temperature / Target | Per-nozzle current and target temperature (auto-detects up to 4) |
| T0–T3 Filament | Loaded filament, e.g. `PLA Basic`, or `Empty`. Colour, vendor and more as attributes ([details](#filament-type-and-colour)) |
| Chamber / extra temperature sensors | Every Klipper `temperature_sensor` (e.g. chamber, MCU) |
| Panda Breath Temperature | Chamber heater temperature, if present ([details](#panda-breath--dragonbreath-chamber-heater)) |
| Part Cooling Fan | Fan speed in % |
| Print Speed / Flow Rate | Current speed and flow overrides in % |
| Klipper State | `ready` / `startup` / `shutdown` / `error` |
| Toolhead Position | X/Y/Z coordinates (with individual attributes) |

### Binary sensors
| Entity | Description |
|---|---|
| Printer Ready | On when Klipper reports `ready` |
| Printing | On while a print is running |
| Paused | On while a print is paused |
| Error | On when a Klipper/print error is active (message in attributes) |
| Filament runout | One per filament sensor (the U1's `filament_motion_sensor e0…e3` and any `filament_switch_sensor`); on when filament is **not** detected |

### Buttons
| Button | Action |
|---|---|
| Pause Print / Resume Print / Cancel Print | Control the active print |
| Emergency Stop | Immediately halts all motion |
| Home All Axes | `G28` |
| Home X / Y / Z | Home a single axis |
| Restart Klipper | Restarts the Klipper process |

### Controls (numbers, selects, switches)
| Entity | Description |
|---|---|
| Bed Target Temperature | Set the bed temperature (0–120 °C) |
| T0–T3 Target Temperature | Set each nozzle temperature (0–300 °C) |
| Part Cooling Fan Speed | 0–100 % |
| Print Speed Override / Flow Rate Override | `M220` / `M221` |
| Panda Breath Target | Chamber heater target; `0` turns it off, otherwise between its min and max (e.g. 15–70 °C) |
| Print File | Pick a file from the printer and start printing it ([details](#starting-a-print)) |
| Active Tool | Switch between T0–T3 |
| Work Light | The cavity light (reads its real state from the printer) |
| DragonBreath Filter | Chamber filter fan, if present |

### Starting a print
The **Print File** select lists the G-code files on the printer, newest
first. New uploads appear automatically. **Choosing a file starts printing
it immediately**, so double-check the file and that the bed is clear.

It's a dropdown, so on a dashboard use the tile card's *Select options*
feature (not *Toggle*), or an entities card:

```yaml
type: tile
entity: select.<printer>_print_file
features:
  - type: select-options
```

While a print is running or paused, choosing a file shows an error instead
of starting a second print. The entity is unavailable while the printer
isn't ready or has no files. You can also start a print from an automation
with `snapmaker_u1.start_print`.

### Camera
- **Webcam**: camera URLs are discovered from Moonraker (`/server/webcams/list`),
  falling back to `/webcam/snapshot.jpg` and `/webcam/stream.mjpg`.
- If the snapshot endpoint fails, a frame is taken from the MJPEG stream
  instead. That also wakes a stock U1 camera that has gone idle.
- The live view proxies the printer's MJPEG stream directly.
- The URLs in use are shown as `snapshot_url` / `stream_url` attributes on
  the camera entity.

> **Note:** The camera is served by the **printer**, not by Home Assistant.
> Test camera URLs against the printer's IP, e.g.
> `http://<printer-ip>/webcam/snapshot.jpg`, not your Home Assistant address.

### Services
| Service | Description |
|---|---|
| `snapmaker_u1.execute_gcode` | Send any G-code command or macro |
| `snapmaker_u1.set_bed_temperature` | Set heated-bed target (°C) |
| `snapmaker_u1.set_nozzle_temperature` | Set a nozzle target (°C) by extruder index |
| `snapmaker_u1.start_print` | Start a file that is already on the printer |

All services take the printer's `config_entry_id`.

### Diagnostics
**Settings → Devices & Services → Snapmaker U1 → ⋮ → Download diagnostics**
gives you the printer state, connection status and camera URLs. Your API key
is redacted automatically.

---

## Dashboard card

The integration ships its own dashboard card, styled like the
[Printer Tools](https://github.com/62fixolab/Printer-Tools-App) app. The
integration adds it to your dashboard resources automatically (Settings →
Dashboards → ⋮ → Resources), so there's nothing extra to install. If you manage
dashboards in YAML mode, it's loaded as an extra frontend module instead.

**Edit dashboard → Add card → search "Snapmaker U1"**, or in YAML:

```yaml
type: custom:snapmaker-u1-card
```

| Printing (light) | Printing (dark) |
|---|---|
| ![Card while printing, light theme](docs/images/card-printing-light.png) | ![Card while printing, dark theme](docs/images/card-printing-dark.png) |

| Paused (teal accent, camera hidden) | Idle with filament alert (blue accent) |
|---|---|
| ![Card while paused](docs/images/card-paused-dark.png) | ![Card while idle with a filament runout alert](docs/images/card-idle-light.png) |

What it shows:
- 📷 **Live camera** at the top. Tap it to open the full camera view. If live
  video isn't available, it falls back to a snapshot every 5 seconds.
- 🟠 **Status pill**: Printing, Paused, Complete, Ready, Error or Offline.
- ⭕ **Progress ring** with the file name, layer (e.g. 142 / 230), time
  remaining, ETA and elapsed time.
- 🌡️ **Temperature tiles** for each nozzle (T0–T3, with the active tool
  highlighted), the bed, the chamber and the Panda Breath (if present).
  Tap a tile to set its target.
- 🔥 **Chamber heater presets**: one-tap filament buttons (PLA, ABS, PC, …) for a
  Panda Breath / DragonBreath ([details](#filament-presets)).
- 🎨 **Loaded filament** on each nozzle tile: a colour dot and the material
  (e.g. `PLA Basic`). A dashed ring means the nozzle is empty.
- **Speed, flow and fan** at a glance. Tap to adjust.
- ⚠️ **Alerts** for printer errors and filament runout.
- 🎛️ **Controls**: Pause/Resume, Cancel, Light, Filter (Panda Breath), Home
  and E-Stop. Cancel and E-Stop need a second tap to confirm.

Options (all optional; all except `chamber_presets` are also in the visual editor):

| Option | Default | Description |
|---|---|---|
| `entry_id` | first printer | Which printer to show, if you have more than one |
| `title` | printer name | Card title |
| `accent_color` | `theme` | `theme`, `orange`, `blue`, `green`, `purple`, `red`, `teal`, `pink`, or any CSS color like `"#ff6600"` |
| `show_camera` | `true` | Show the camera feed |
| `camera_mode` | `live` | `live` video or `snapshot` (refreshed every 5 seconds) |
| `show_controls` | `true` | Show the control buttons |
| `show_chamber_presets` | `true` | Show filament preset buttons for a Panda Breath / DragonBreath |
| `chamber_presets` | see below | Your own preset list (YAML only), e.g. `[{name: ABS, temp: 55}, {name: PLA, temp: 0}]` |

Example:

```yaml
type: custom:snapmaker-u1-card
title: Living Room U1
accent_color: orange
camera_mode: live
```

Light and dark mode follow your Home Assistant theme. Renamed entities keep
working, because the card looks entities up through the integration rather
than by entity ID.

---

## Filament type and colour

The U1 knows what's loaded in each nozzle. With Snapmaker spools it reads
the RFID tag; with other spools it uses what you picked on the touchscreen.
The integration reads this from the printer (Klipper's `print_task_config`
object), so it works on stock firmware with no setup.

Each nozzle gets a **T0–T3 Filament** sensor:

| | Example |
|---|---|
| State | `PLA Basic`, `PETG`, or `Empty` |
| `color` | `#FF6A00` (`null` while unknown) |
| `alpha` | `255` (transparency, 0–255) |
| `material` / `sub_type` | `PLA` / `Basic` |
| `vendor` | `Snapmaker` |
| `loaded` | `true` when filament is in the nozzle |
| `official` | `true` for a Snapmaker RFID spool |

It updates live when you load or change a spool. Example template:

```yaml
{{ state_attr('sensor.<printer>_t0_filament', 'color') }}
```

Notes:
- Third-party spools don't have a Snapmaker RFID tag, so their type and
  colour only appear after you set them on the printer's touchscreen.
- Multi-colour (gradient) spools show their first colour.

---

## Panda Breath / DragonBreath chamber heater

If you run the [U1 Extended Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware)
and have enabled a BIQU **Panda Breath** (or **DragonBreath**) in its Firmware
Config page (`http://<printer-ip>/firmware-config/`), the printer's Klipper
controls the heater. This integration picks it up automatically, with no
extra setup:

| Entity | What it does |
|---|---|
| `sensor.<printer>_panda_breath_temperature` | Chamber heater temperature (attributes: target, power, max_temp) |
| `number.<printer>_panda_breath_target` | Set the chamber target (°C). `0` turns heating off; any other value must be between the heater's Klipper `min_temp` and `max_temp` (e.g. 15–70 °C) |
| `switch.<printer>_dragonbreath_filter` | Filter fan (DragonBreath). The device may refuse to turn it on while heating |

On the dashboard card it appears as a **Panda Breath** temperature tile (tap
to set the target) and a **Filter** button.

Notes:
- Restart Home Assistant (or reload the integration) after enabling the heater
  on the printer, so the new Klipper objects are discovered.
- Any Klipper `heater_generic` is treated as a chamber heater, and any
  `output_pin` with "filter" or "breath" in its name as a filter switch.
- With **stock** U1 firmware the Panda Breath isn't visible to the printer.
  In that case, use the Panda Breath's own firmware 1.0.4 MQTT /
  Home Assistant discovery instead.

### Filament presets

The card shows a row of one-tap presets under the temperature tiles. Tap a
filament type and the chamber heater goes to its recommended temperature:

| Preset | Chamber target |
|---|---|
| PLA, TPU | Off (these print best in a cool chamber) |
| PETG | 35 °C (a little warmth helps layer adhesion, far below where PETG softens) |
| ABS, ASA | 50 °C |
| PA (nylon) | 55 °C |
| PC | 60 °C |

- The preset that's currently set is highlighted (Off presets in grey), and a small dot marks
  the filament type that's loaded in a nozzle.
- Targets are kept within the heater's minimum and maximum from the printer's config.
- To use your own values, set `chamber_presets` in the card's YAML (⋮ → *Show code editor*):

```yaml
type: custom:snapmaker-u1-card
chamber_presets:
  - name: PETG
    temp: 40
  - name: ABS
    temp: 55
  - name: PC
    temp: 60
```

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
   - **IP Address**: the local IP of your printer (e.g. `192.168.1.42`)
   - **Port**: `80` by default (Moonraker is proxied through nginx)
   - **API Key**: leave blank unless you have configured Moonraker authentication

The integration tests the connection before saving. If it succeeds, all
entities are created immediately.

- **Changed the printer's IP?** Use **⋮ → Reconfigure** on the integration.
- **Polling interval**: **Configure** sets how often state is refreshed while
  the WebSocket is unavailable.
- **Temperature unit**: **Configure → Temperature unit** picks the unit for
  every printer temperature (sensors, setpoints, the dashboard card and its
  presets). It defaults to **°C**, the usual unit for 3D printing, even if
  Home Assistant itself is set to °F. Choose °F, or *Home Assistant default*
  to follow your system setting.

---

## Authentication

The stock U1 firmware configures Moonraker with trusted LAN clients, so **no
API key is required** if Home Assistant is on the same network as the printer.

If you use the
[Extended Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware)
with forced logins enabled, generate an API key in Moonraker and paste it into
the API Key field.

---

## Automations

Device triggers are available under the printer's device: **Print started**,
**Print complete**, **Print failed**, **Print paused** and **Print cancelled**.
The same moments fire these events:

| Event | When |
|---|---|
| `snapmaker_u1_print_started` | A print starts |
| `snapmaker_u1_print_complete` | A print finishes |
| `snapmaker_u1_print_failed` | A print errors |
| `snapmaker_u1_print_paused` | A print is paused |
| `snapmaker_u1_print_cancelled` | A print is cancelled |

Example: notify when a print is done.

```yaml
automation:
  - alias: "U1 print finished"
    trigger:
      - platform: event
        event_type: snapmaker_u1_print_complete
    action:
      - service: notify.notify
        data:
          message: "Your Snapmaker U1 print is done!"
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot connect" during setup | Verify the IP, confirm the printer is on and that Fluidd loads in your browser at that address |
| Everything shows `unavailable` | The printer can't be reached (switched off, or off the network). Entities come back by themselves once it answers again; if they don't, check Klipper logs via Fluidd |
| Printer was off when Home Assistant started | Nothing to do: the integration shows *Retrying setup* and connects by itself once the printer is back on. If new heaters or sensors appear after Klipper starts, it reloads itself to add them |
| Camera shows no image / "500: Internal Server Error" snapshot | The integration couldn't get an image from the printer. Open `http://<printer-ip>/webcam/` and `http://<printer-ip>/webcam/snapshot.jpg` in a browser (use the **printer's** IP). If neither shows the camera, the printer isn't exposing its camera over HTTP. The Snapmaker app can reach the camera through its own service, so seeing the feed there doesn't guarantee an HTTP endpoint exists. The [Extended Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware) provides `/webcam/snapshot.jpg` and a live stream. Check the camera entity's `snapshot_url` / `stream_url` attributes and the Home Assistant log (it lists every URL tried) |
| Dashboard card not in the card picker / "Custom element doesn't exist" | Update to 1.10.0 or later, **restart Home Assistant**, then hard-refresh the browser (Ctrl+Shift+R); in the mobile app, use **Reset frontend cache** in the Companion app settings. The integration adds `/snapmaker_u1_static/snapmaker-u1-card.js` to Settings → Dashboards → ⋮ → **Resources** for you (and updates an entry you added by hand). Add the card to a dashboard you created (Settings → Dashboards); newer Home Assistant versions generate the default *Overview* automatically. YAML-mode dashboards: add that URL under `lovelace: resources:` as a `module` |
| Card shows no nozzle tiles | Update to 1.7.2 or later |
| Print File shows "Unavailable" | Update to 1.7.1 or later (older versions misread the printer's file list). It's also unavailable while the printer isn't ready or has no files in its storage |
| Panda Breath entities missing | Enable it in the printer's Firmware Config page first (Extended Firmware only), then reload the integration |
| Temperatures show 0 after startup | Klipper may still be initialising; values update within seconds once it's ready |

---

## Technical notes

- **Protocol**: Moonraker WebSocket (JSON-RPC 2.0) + HTTP REST
- **IoT class**: `local_push` (push updates via WebSocket, no cloud dependency)
- **Reconnection**: Exponential back-off from 5 s to 60 s, always waiting between attempts (even when the printer closes the socket itself)
- **Availability**: When the connection drops, the integration polls straight away; if the printer doesn't answer, every entity (camera included) shows `unavailable` instead of its last value
- **Extruders**: Up to 4 nozzles auto-detected (T0–T3)
- **Dashboard card**: served by the integration from `/snapmaker_u1_static/`
  and registered as a dashboard resource (an extra frontend module in YAML
  mode); it reads entities through the `snapmaker_u1/entities` websocket command
- **File list**: kept up to date from Moonraker's `notify_filelist_changed`
  details (files and folders); a full re-fetch only happens for a
  `root_update`, at most once a minute
- **Startup**: if the printer is unreachable, setup is retried automatically
- **HA version**: Home Assistant 2024.1+
- **Icon**: shipped in `custom_components/snapmaker_u1/brand/` (light and dark,
  256 px and 512 px), which Home Assistant 2026.3+ uses automatically. Older
  versions show a generic icon. Source: `docs/images/icon.svg`

---

## Credits

Architecture inspired by [ha-bambulab](https://github.com/greghesp/ha-bambulab)
by Greg Hesp and Adrian Garside. Dashboard card styled after
[Printer Tools](https://github.com/62fixolab/Printer-Tools-App) by FixoLab.
Panda Breath support builds on the
[U1 Extended Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware)
by paxx12.

Moonraker API documentation: https://moonraker.readthedocs.io
