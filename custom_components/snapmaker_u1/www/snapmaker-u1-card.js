/*
 * Snapmaker U1 dashboard card.
 *
 * Shipped with the snapmaker_u1 integration and loaded automatically, so
 * "custom:snapmaker-u1-card" can be added to any dashboard. Shows the live
 * camera, print progress, layers, time left, ETA, nozzle/bed/chamber
 * temperatures and print controls for one printer.
 */

const CARD_VERSION = "1.14.1";
const DOMAIN = "snapmaker_u1";

const ACCENTS = {
  theme: "var(--primary-color)",
  orange: "#ff8a00",
  blue: "#2f80ed",
  green: "#27ae60",
  purple: "#8e5cf6",
  red: "#eb5757",
  teal: "#14b8a6",
  pink: "#ec4899",
};

const STATUS = {
  printing: { label: "Printing", icon: "mdi:printer-3d-nozzle", tone: "accent" },
  paused: { label: "Paused", icon: "mdi:pause-circle", tone: "warning" },
  complete: { label: "Complete", icon: "mdi:check-circle", tone: "success" },
  cancelled: { label: "Cancelled", icon: "mdi:close-circle", tone: "muted" },
  error: { label: "Error", icon: "mdi:alert-circle", tone: "error" },
  standby: { label: "Ready", icon: "mdi:printer-3d", tone: "muted" },
  offline: { label: "Offline", icon: "mdi:lan-disconnect", tone: "error" },
};

// Chamber heater (Panda Breath / DragonBreath) presets per filament type.
// PLA and TPU print best with the chamber heater off, PETG likes a little
// warmth, and engineering filaments benefit from a warm chamber. Values are capped at the heater's
// max (60 °C on a Panda Breath). Override with `chamber_presets`.
const DEFAULT_CHAMBER_PRESETS = [
  { name: "PLA", temp: 0 },
  { name: "PETG", temp: 35 },
  { name: "TPU", temp: 0 },
  { name: "ABS", temp: 50 },
  { name: "ASA", temp: 50 },
  { name: "PA", temp: 55 },
  { name: "PC", temp: 60 },
];

const CONFIRM_MS = 4000;
const SNAPSHOT_REFRESH_MS = 5000;
const LIVE_FIRST_FRAME_MS = 8000;

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

const num = (state) => {
  if (!state || state.state === "unavailable" || state.state === "unknown") return null;
  const value = Number(state.state);
  return Number.isFinite(value) ? value : null;
};

const formatDuration = (seconds) => {
  if (seconds == null || seconds < 0) return "–";
  const s = Math.round(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
};

// Update `parent` to match `html` while keeping existing nodes wherever
// possible. The printer pushes several updates a second; rebuilding the
// buttons with innerHTML each time replaced the element under the user's
// finger mid-tap, so taps were lost.
const patchHTML = (parent, html) => {
  const template = document.createElement("template");
  template.innerHTML = html;
  patchChildren(parent, template.content);
};

const patchChildren = (from, to) => {
  const toNodes = Array.from(to.childNodes);
  toNodes.forEach((toNode, i) => {
    const fromNode = from.childNodes[i];
    if (!fromNode) {
      from.appendChild(toNode.cloneNode(true));
    } else if (fromNode.nodeType !== toNode.nodeType || fromNode.nodeName !== toNode.nodeName) {
      from.replaceChild(toNode.cloneNode(true), fromNode);
    } else if (fromNode.nodeType === Node.ELEMENT_NODE) {
      for (const { name } of Array.from(fromNode.attributes)) {
        if (!toNode.hasAttribute(name)) fromNode.removeAttribute(name);
      }
      for (const { name, value } of Array.from(toNode.attributes)) {
        if (fromNode.getAttribute(name) !== value) fromNode.setAttribute(name, value);
      }
      patchChildren(fromNode, toNode);
    } else if (fromNode.nodeValue !== toNode.nodeValue) {
      fromNode.nodeValue = toNode.nodeValue;
    }
  });
  while (from.childNodes.length > toNodes.length) from.removeChild(from.lastChild);
};

// Mirrors chamber_label() in definitions.py.
const chamberLabel = (name) => {
  const lower = String(name).toLowerCase();
  for (const [raw, label] of [["dragonbreath", "DragonBreath"], ["panda_breath", "Panda Breath"]]) {
    if (lower.startsWith(raw)) {
      const rest = lower.slice(raw.length).replace(/^_+|_+$/g, "").replace(/_/g, " ");
      return `${label} ${rest.replace(/\b\w/g, (c) => c.toUpperCase())}`.trim();
    }
  }
  return lower.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
};

// "DragonBreath" -> "Dragon<wbr>Breath" so long camel-case names wrap like
// "Panda Breath" instead of running out of the tile.
const breakableLabel = (label) => esc(label).replace(/([a-z])([A-Z])/g, "$1<wbr>$2");

const prettyFilename = (name) =>
  String(name || "")
    .split("/")
    .pop()
    .replace(/\.(gcode|g|3mf|bgcode)$/i, "");

class SnapmakerU1Card extends HTMLElement {
  static getConfigElement() {
    return document.createElement("snapmaker-u1-card-editor");
  }

  static getStubConfig() {
    return {
      show_camera: true,
      camera_mode: "live",
      show_controls: true,
      show_chamber_presets: true,
      show_top_cover: true,
      accent_color: "theme",
    };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._printers = null;
    this._loading = false;
    this._armed = null;
    this._signature = "";
    this._cameraSrc = "";
    this._cameraFallback = false;
    this._snapshotTimer = null;
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => this._onChange(ev));
  }

  setConfig(config) {
    this._config = {
      show_camera: true,
      camera_mode: "live",
      show_controls: true,
      accent_color: "theme",
      ...(config && typeof config === "object" ? config : {}),
    };
    this._signature = "";
    this._cameraFallback = false;
    this._buildSkeleton();
    if (this._hass) this._safeUpdate();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._printers && !this._loading) this._loadPrinters();
    else if (this._registryChanged()) this._scheduleReload();
    this._safeUpdate();
  }

  // The entity registry changes when the integration reloads, e.g. after a
  // Top Cover is plugged in. Re-read the entity mapping so new entities show
  // up without reloading the browser.
  _registryKey() {
    const hass = this._hass;
    return hass.entities || Object.keys(hass.states).length;
  }

  _registryChanged() {
    return this._loadedKey !== undefined && this._registryKey() !== this._loadedKey;
  }

  _scheduleReload() {
    // Restart the wait only when the registry changed again, not on every
    // state update (those arrive constantly while printing).
    const key = this._registryKey();
    if (this._reloadTimer && key === this._scheduledKey) return;
    this._scheduledKey = key;
    clearTimeout(this._reloadTimer);
    // Wait for a reload's remove/add burst to settle first.
    this._reloadTimer = setTimeout(() => {
      this._reloadTimer = null;
      if (this._loading) this._scheduleReload();
      else if (this._registryChanged()) this._loadPrinters();
    }, 1500);
  }

  // Home Assistant replaces a card that throws while updating with a blank
  // "Configuration error" box for good. Catch everything instead, keep the
  // card alive, and show what went wrong so it can be reported.
  _safeUpdate() {
    try {
      this._update();
      this._lastError = null;
    } catch (err) {
      this._signature = "";
      const message = (err && (err.message || String(err))) || "Unknown error";
      if (message !== this._lastError) {
        this._lastError = message;
        console.error("snapmaker-u1-card:", err);
      }
      if (this._contentEl) {
        const where = ((err && err.stack) || "").split("\n").slice(1, 2).join("").trim();
        this._contentEl.innerHTML = `
          <div class="alert card-error">
            <ha-icon icon="mdi:alert-circle"></ha-icon>
            <span>Snapmaker card error: ${esc(message)}${where ? `<br><small>${esc(where)}</small>` : ""}</span>
          </div>`;
      }
    }
  }

  connectedCallback() {
    this._connected = true;
    this._signature = "";
    if (this._hass) this._safeUpdate();
  }

  disconnectedCallback() {
    // Close the MJPEG stream so hidden cards don't keep the camera busy.
    this._connected = false;
    this._setCamera("");
    clearTimeout(this._reloadTimer);
    this._reloadTimer = null;
  }

  getCardSize() {
    return this._config?.show_camera === false ? 6 : 10;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  // ------------------------------------------------------------------
  // Data
  // ------------------------------------------------------------------

  async _loadPrinters() {
    this._loading = true;
    this._loadedKey = this._registryKey();
    try {
      const result = await this._hass.callWS({ type: `${DOMAIN}/entities` });
      this._printers = result.printers || [];
    } catch (err) {
      this._error = `Could not load Snapmaker U1 printers: ${err.message || err}`;
      this._printers = [];
    }
    this._loading = false;
    this._signature = "";
    this._safeUpdate();
    if (this._registryChanged()) this._scheduleReload();  // changed while loading
  }

  get _printer() {
    if (!this._printers || !this._printers.length) return null;
    const wanted = this._config.entry_id;
    return (wanted && this._printers.find((p) => p.entry_id === wanted)) || this._printers[0];
  }

  _eid(role) {
    return this._printer?.entities?.[role];
  }

  _state(role) {
    const eid = this._eid(role);
    return eid ? this._hass.states[eid] : undefined;
  }

  _roles(prefix) {
    return Object.keys(this._printer?.entities || {})
      .filter((role) => role.startsWith(prefix))
      // Skip leftovers the printer no longer provides (e.g. a Panda Breath
      // replaced by a DragonBreath): HA keeps them as "restored" entities.
      .filter((role) => !this._state(role)?.attributes?.restored)
      .sort();
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------

  _buildSkeleton() {
    const accent = ACCENTS[this._config.accent_color] || this._config.accent_color || ACCENTS.theme;
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card style="--sm-accent:${esc(accent)}">
        <div class="camera" data-action="camera" hidden>
          <img alt="Printer camera" />
          <div class="camera-badge"><span class="dot"></span><span class="camera-label">LIVE</span></div>
          <div class="camera-error"><ha-icon icon="mdi:camera-off"></ha-icon><span>Camera unavailable</span></div>
        </div>
        <div class="content"></div>
      </ha-card>`;
    this._contentEl = this.shadowRoot.querySelector(".content");
    this._cameraEl = this.shadowRoot.querySelector(".camera");
    this._imgEl = this.shadowRoot.querySelector(".camera img");
    this._imgEl.addEventListener("error", () => {
      // Live MJPEG failed: fall back to refreshed snapshots.
      if (!this._cameraFallback && this._config.camera_mode !== "snapshot") {
        this._cameraFallback = true;
        this._cameraSrc = "";
        this._updateCamera();
        return;
      }
      // Snapshots failing too: say so instead of showing a black box. The
      // snapshot timer keeps retrying, and a successful load clears this.
      if (this._cameraSrc) this._cameraEl.classList.add("is-error");
    });
    this._imgEl.addEventListener("load", () => this._cameraEl.classList.remove("is-error"));
    this._cameraSrc = "";
  }

  _update() {
    if (!this._hass || !this._config || !this._contentEl) return;
    if (!this._printers) {
      this._contentEl.innerHTML = `<div class="empty">Loading…</div>`;
      return;
    }
    if (!this._printer) {
      this._contentEl.innerHTML = `<div class="empty">${esc(
        this._error || "No Snapmaker U1 printer found. Add the Snapmaker U1 integration first."
      )}</div>`;
      return;
    }

    this._updateCamera();

    // Only re-render when something the card shows has changed.
    const entityIds = Object.values(this._printer.entities);
    const signature =
      entityIds
        .map((eid) => {
          const s = this._hass.states[eid];
          // last_updated also changes when only attributes change (e.g. a
          // new spool colour or heater target with the same state).
          return s ? `${s.state}|${s.last_updated ?? ""}|${s.attributes.klipper_message ?? ""}` : "";
        })
        .join(";") + `|${this._armed?.action ?? ""}|${this._popOpen ? 1 : 0}`;
    if (signature === this._signature) return;
    this._signature = signature;
    patchHTML(this._contentEl, this._renderContent());
    // patchHTML only updates the value attribute; move sliders to the new
    // value unless the user is dragging one.
    for (const input of this._contentEl.querySelectorAll("input[type=range]")) {
      if (this.shadowRoot.activeElement !== input) input.value = input.getAttribute("value");
    }
  }

  _updateCamera() {
    const camera = this._state("camera.webcam");
    const show = this._config.show_camera !== false && camera && camera.state !== "unavailable";
    this._cameraEl.hidden = !show;
    if (!show || !this._connected) {
      this._setCamera("");
      return;
    }
    const snapshot = this._config.camera_mode === "snapshot" || this._cameraFallback;
    this.shadowRoot.querySelector(".camera-label").textContent = snapshot ? "SNAPSHOT" : "LIVE";
    this._cameraEl.classList.toggle("is-live", !snapshot);
    const token = camera.attributes.access_token;
    if (snapshot) {
      if (!this._snapshotTimer) {
        const refresh = () => {
          const cam = this._state("camera.webcam");
          if (cam?.attributes.entity_picture) {
            this._setCamera(`${cam.attributes.entity_picture}&_t=${Date.now()}`, true);
          }
        };
        refresh();
        this._snapshotTimer = setInterval(refresh, SNAPSHOT_REFRESH_MS);
      }
    } else if (token) {
      const src = `/api/camera_proxy_stream/${camera.entity_id}?token=${token}`;
      if (src !== this._cameraSrc) {
        this._setCamera(src);
        // A stream that stays open without sending a frame never fires
        // "error"; treat no picture after a few seconds as a failure too.
        clearTimeout(this._liveWatchdog);
        this._liveWatchdog = setTimeout(() => {
          if (this._cameraSrc === src && !this._imgEl.naturalWidth && !this._cameraFallback) {
            this._cameraFallback = true;
            this._cameraSrc = "";
            this._updateCamera();
          }
        }, LIVE_FIRST_FRAME_MS);
      }
    }
  }

  _setCamera(src, keepTimer = false) {
    if (!src) clearTimeout(this._liveWatchdog);
    if (!keepTimer && this._snapshotTimer) {
      clearInterval(this._snapshotTimer);
      this._snapshotTimer = null;
    }
    if (src === this._cameraSrc || !this._imgEl) return;
    this._cameraSrc = src;
    if (src) this._imgEl.src = src;
    else this._imgEl.removeAttribute("src");
  }

  _status() {
    const ready = this._state("binary_sensor.printer_ready");
    const printState = this._state("sensor.print_state");
    if (!printState || printState.state === "unavailable" || ready?.state === "off") {
      return "offline";
    }
    if (this._state("binary_sensor.has_error")?.state === "on") return "error";
    return STATUS[printState.state] ? printState.state : "standby";
  }

  _renderContent() {
    const status = this._status();
    const info = STATUS[status];
    const active = status === "printing" || status === "paused";
    const light = this._state("switch.work_light");

    return `
      <div class="header">
        <div class="title">
          <ha-icon icon="mdi:printer-3d"></ha-icon>
          <span>${esc(this._config.title || this._printer.title || "Snapmaker U1")}</span>
        </div>
        <div class="pill tone-${info.tone}">
          <ha-icon icon="${info.icon}"></ha-icon>${info.label}
        </div>
      </div>
      ${this._renderAlerts()}
      ${active ? this._renderProgress(status) : this._renderIdle(status)}
      ${this._renderTemps()}
      ${this._config.show_chamber_presets !== false ? this._renderChamberPresets() : ""}
      ${this._config.show_top_cover !== false ? this._renderTopCover() : ""}
      ${this._renderStats()}
      ${this._config.show_controls !== false ? this._renderControls(status, light) : ""}
      ${this._popOpen ? this._renderPopStatus() : ""}`;
  }

  _renderAlerts() {
    const alerts = [];
    const error = this._state("binary_sensor.has_error");
    if (error?.state === "on") {
      alerts.push(error.attributes.klipper_message || "The printer reported an error.");
    }
    for (const role of this._roles("binary_sensor.filament_")) {
      const s = this._state(role);
      if (s?.state === "on") {
        alerts.push(`${s.attributes.friendly_name || "Filament sensor"}: filament not detected`);
      }
    }
    return alerts
      .map(
        (text) =>
          `<div class="alert"><ha-icon icon="mdi:alert"></ha-icon><span>${esc(text)}</span></div>`
      )
      .join("");
  }

  _renderProgress(status) {
    const progress = num(this._state("sensor.print_progress")) ?? 0;
    const layer = num(this._state("sensor.current_layer"));
    const layers = num(this._state("sensor.total_layers"));
    const remaining = num(this._state("sensor.time_remaining"));
    const elapsed = num(this._state("sensor.print_duration"));
    const etaState = this._state("sensor.print_eta");
    let eta = "–";
    if (etaState && !["unknown", "unavailable"].includes(etaState.state)) {
      const date = new Date(etaState.state);
      if (!Number.isNaN(date.getTime())) {
        const sameDay = date.toDateString() === new Date().toDateString();
        const options = { ...(sameDay ? {} : { weekday: "short" }), hour: "2-digit", minute: "2-digit" };
        try {
          eta = date.toLocaleString(this._hass.locale?.language || undefined, options);
        } catch (err) {
          // Unknown language tag: fall back to the browser's locale.
          eta = date.toLocaleString(undefined, options);
        }
      }
    }
    const filename = prettyFilename(this._state("sensor.filename")?.state);
    const radius = 42;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference * (1 - Math.min(Math.max(progress, 0), 100) / 100);

    return `
      <div class="progress">
        <div class="ring ${status === "paused" ? "paused" : ""}">
          <svg viewBox="0 0 100 100">
            <circle class="track" cx="50" cy="50" r="${radius}"></circle>
            <circle class="bar" cx="50" cy="50" r="${radius}"
              stroke-dasharray="${circumference.toFixed(2)}"
              stroke-dashoffset="${offset.toFixed(2)}"></circle>
          </svg>
          <div class="ring-label"><span class="pct">${Math.round(progress)}</span><span class="unit">%</span></div>
        </div>
        <div class="job">
          <div class="filename" title="${esc(filename)}">${esc(filename || "Printing")}</div>
          <div class="facts">
            ${this._fact("mdi:layers-triple", "Layer", layer != null && layers ? `${layer} / ${layers}` : "–")}
            ${this._fact("mdi:timer-sand", "Remaining", formatDuration(remaining))}
            ${this._fact("mdi:clock-end", "ETA", eta)}
            ${this._fact("mdi:timer-outline", "Elapsed", formatDuration(elapsed))}
          </div>
        </div>
      </div>`;
  }

  _renderIdle(status) {
    const filename = prettyFilename(this._state("sensor.filename")?.state);
    const text =
      status === "offline"
        ? "Printer is not reachable"
        : status === "complete" && filename
          ? `Finished ${filename}`
          : status === "cancelled" && filename
            ? `Cancelled ${filename}`
            : "Ready to print";
    return `<div class="idle"><ha-icon icon="${STATUS[status].icon}"></ha-icon><span>${esc(text)}</span></div>`;
  }

  _fact(icon, label, value) {
    return `
      <div class="fact">
        <ha-icon icon="${icon}"></ha-icon>
        <div><div class="fact-label">${label}</div><div class="fact-value">${esc(value)}</div></div>
      </div>`;
  }

  _renderTemps() {
    const tiles = [];
    const activeTool = this._state("select.active_tool")?.state;
    // "extruder" is T0, "extruder1" is T1, and so on.
    // Nozzle sensor roles are "sensor.extruder_extruder_temperature" (the
    // unique ID repeats the extruder key); accept the short form as well.
    const extruders = this._roles("sensor.extruder")
      .map((role) => role.match(/^sensor\.(?:extruder\d*_)?(extruder(\d*))_temperature$/))
      .filter(Boolean)
      .map(([role, key, n]) => ({ role, key, index: n ? Number(n) : 0 }))
      .sort((a, b) => a.index - b.index);
    extruders.forEach(({ role, key, index }) => {
      const tool = `T${index}`;
      tiles.push(
        this._tempTile({
          label: tool,
          icon: "mdi:printer-3d-nozzle",
          current: num(this._state(role)),
          target: num(this._state(role.replace(/_temperature$/, "_target"))),
          unit: this._state(role)?.attributes.unit_of_measurement,
          moreInfo: this._eid(`number.${key}_target_setpoint`) || this._eid(role),
          active: extruders.length > 1 && activeTool === tool,
          filament: this._state(`sensor.${key}_filament`),
        })
      );
    });
    if (this._eid("sensor.bed_temperature")) {
      const bed = this._state("sensor.bed_temperature");
      tiles.push(
        this._tempTile({
          label: "Bed",
          icon: "mdi:radiator",
          current: num(bed),
          target: num(this._state("sensor.bed_target")),
          unit: bed?.attributes.unit_of_measurement,
          moreInfo: this._eid("number.bed_target") || this._eid("sensor.bed_temperature"),
        })
      );
    }
    for (const role of this._roles("sensor.chamber_")) {
      if (!/chamber|cavity/i.test(role.slice("sensor.chamber_".length))) continue;
      const s = this._state(role);
      tiles.push(
        this._tempTile({
          label: "Chamber",
          icon: "mdi:thermometer",
          current: num(s),
          target: null,
          unit: s?.attributes.unit_of_measurement,
          moreInfo: this._eid(role),
        })
      );
    }
    // Chamber heaters (Panda Breath / DragonBreath on U1 Extended Firmware)
    for (const role of this._roles("sensor.heater_")) {
      const match = role.match(/^sensor\.heater_(.+)_temperature$/);
      if (!match) continue;
      const s = this._state(role);
      tiles.push(
        this._tempTile({
          label: chamberLabel(match[1]),
          icon: "mdi:heat-wave",
          current: num(s),
          // The number entity is unit-converted like the sensor; the raw
          // "target" attribute is always °C.
          target: num(this._state(`number.heater_${match[1]}_target`)) ?? s?.attributes.target ?? 0,
          unit: s?.attributes.unit_of_measurement,
          moreInfo: this._eid(`number.heater_${match[1]}_target`) || this._eid(role),
        })
      );
    }
    return tiles.length ? `<div class="temps">${tiles.join("")}</div>` : "";
  }

  _tempTile({ label, icon, current, target, unit, moreInfo, active = false, filament = null }) {
    const u = unit || "°C";
    // A target of 0 °C means "off"; shown in °F that is 32.
    const heating = target != null && target > (u === "°F" ? 32.5 : 0);
    return `
      <button class="temp ${heating ? "heating" : ""} ${active ? "active" : ""}"
        data-action="more-info" data-entity="${esc(moreInfo || "")}">
        <div class="temp-head"><ha-icon icon="${icon}"></ha-icon><span class="temp-label" title="${esc(label)}">${breakableLabel(label)}</span>${this._swatch(filament)}</div>
        <div class="temp-value">${current != null ? Math.round(current) : "–"}<span>${esc(u)}</span></div>
        <div class="temp-target">${heating ? `→ ${Math.round(target)}${esc(u)}` : target == null ? "&nbsp;" : "Off"}</div>
        ${this._filamentLine(filament)}
      </button>`;
  }

  // Loaded filament from the printer (RFID tag or touchscreen selection).
  _swatch(filament) {
    if (!filament || ["unknown", "unavailable"].includes(filament.state)) return "";
    const color = filament.attributes.color;
    if (filament.attributes.loaded === false || !color) {
      return `<span class="swatch is-empty" title="${esc(filament.state)}"></span>`;
    }
    return `<span class="swatch" style="background:${esc(color)}" title="${esc(color)}"></span>`;
  }

  _filamentLine(filament) {
    if (!filament || ["unknown", "unavailable"].includes(filament.state)) return "";
    const empty = filament.attributes.loaded === false;
    return `<div class="temp-filament ${empty ? "is-empty" : ""}" title="${esc(
      [filament.attributes.vendor, filament.state].filter(Boolean).join(" ")
    )}">${esc(filament.state)}</div>`;
  }

  // One row of filament preset buttons per chamber heater.
  _renderChamberPresets() {
    const presets = (
      Array.isArray(this._config.chamber_presets) ? this._config.chamber_presets : DEFAULT_CHAMBER_PRESETS
    )
      .filter((preset) => preset && typeof preset === "object" && preset.name != null)
      .map((preset) => ({ name: String(preset.name), temp: preset.temp }));
    if (!presets.length) return "";
    // Materials currently loaded, so the matching preset can be marked.
    const loaded = this._roles("sensor.extruder")
      .filter((role) => role.endsWith("_filament"))
      .map((role) => this._state(role)?.attributes)
      .filter((attrs) => attrs && attrs.loaded !== false && attrs.material)
      .map((attrs) => String(attrs.material).toUpperCase());
    const rows = [];
    for (const role of this._roles("sensor.heater_")) {
      const match = role.match(/^sensor\.heater_(.+)_temperature$/);
      const numberId = match && this._eid(`number.heater_${match[1]}_target`);
      const number = numberId && this._hass.states[numberId];
      if (!number) continue;
      // Presets are in °C; the number entity may be shown in °F, in which
      // case its state, max and set_value all use °F.
      const fahrenheit = number.attributes.unit_of_measurement === "°F";
      const toUnit = (c) => (fahrenheit ? Math.round((c * 9) / 5 + 32) : c);
      const maxC = fahrenheit
        ? ((Number(number.attributes.max) || 140) - 32) * (5 / 9)
        : Number(number.attributes.max) || 60;
      // Klipper accepts 0 (off) or min_temp..max_temp; min_temp (°C) is on the sensor.
      const minC = Number(this._state(role)?.attributes.min_temp) || 0;
      const target = Number(number.state);
      const disabled = number.state === "unavailable";
      const unitLabel = fahrenheit ? "°F" : "°C";
      const chips = presets.map(({ name, temp }) => {
        const requested = Number(temp) || 0;
        const off = requested <= 0;
        const value = toUnit(off ? 0 : Math.min(Math.max(requested, minC), maxC));
        const isLoaded = loaded.some((m) => m === String(name).toUpperCase() || m.startsWith(`${String(name).toUpperCase()}-`));
        // Off presets light up too (neutral), so tapping PLA/TPU gives feedback.
        const active = Number.isFinite(target) && Math.abs(target - value) < 0.6;
        const activeClass = active ? (off ? "active-off" : "active") : "";
        return `
          <button class="preset ${activeClass} ${isLoaded ? "loaded" : ""}" data-action="preset"
            data-entity="${esc(numberId)}" data-temp="${value}" ${disabled ? "disabled" : ""}
            title="${esc(`${name}: ${off ? "heater off" : `${value} ${unitLabel}`}${isLoaded ? " (loaded)" : ""}`)}">
            <span class="preset-name">${esc(name)}</span>
            <span class="preset-temp">${off ? "Off" : `${value}°`}</span>
          </button>`;
      });
      rows.push(`
        <div class="presets">
          <div class="presets-title"><ha-icon icon="mdi:heat-wave"></ha-icon>${breakableLabel(chamberLabel(match[1]))} presets</div>
          <div class="preset-row">${chips.join("")}</div>
        </div>`);
    }
    return rows.join("");
  }

  // Official Top Cover: purifier mode buttons plus filter life.
  _renderTopCover() {
    const selectId = this._eid("select.top_cover_mode_select");
    const select = selectId && this._hass.states[selectId];
    // "restored" = left in the registry after the cover was unplugged
    if (!select || select.attributes.restored) return "";
    const disabled = select.state === "unavailable";
    const life = num(this._state("sensor.top_cover_filter_life"));
    const modes = [
      ["off", "Off", "mdi:power"],
      ["cooling", "Cooling", "mdi:snowflake"],
      ["keep_warm", "Keep warm", "mdi:fire"],
    ];
    if (select.state === "preheat") modes.splice(2, 0, ["preheat", "Preheat", "mdi:heat-wave"]);
    const chips = modes.map(([value, label, icon]) => {
      const active = select.state === value;
      return `
        <button class="preset ${active ? (value === "off" ? "active-off" : "active") : ""}" data-action="cover-mode"
          data-entity="${esc(selectId)}" data-option="${value}" ${disabled ? "disabled" : ""} title="Top Cover: ${label}">
          <ha-icon icon="${icon}"></ha-icon>
          <span class="preset-temp">${label}</span>
        </button>`;
    });
    const filter =
      life != null
        ? `<button class="cover-filter ${life <= 10 ? "low" : ""}" data-action="more-info"
            data-entity="${esc(this._eid("sensor.top_cover_filter_life"))}">
            <ha-icon icon="mdi:air-filter"></ha-icon>Filter ${Math.round(life)}%</button>`
        : "";
    return `
      <div class="presets cover">
        <div class="presets-title"><ha-icon icon="mdi:air-purifier"></ha-icon>Top Cover${filter}</div>
        <div class="preset-row">${chips.join("")}</div>
      </div>`;
  }

  _renderStats() {
    const stats = [
      ["sensor.speed_factor", "number.speed_factor", "mdi:speedometer", "Speed"],
      ["sensor.flow_rate", "number.flow_rate", "mdi:water-percent", "Flow"],
      ["sensor.fan_speed", "number.fan_speed", "mdi:fan", "Fan"],
    ]
      .filter(([role]) => this._eid(role))
      .map(([role, setpoint, icon, label]) => {
        const value = num(this._state(role));
        return `
          <button class="stat" data-action="more-info" data-entity="${esc(this._eid(setpoint) || this._eid(role))}">
            <ha-icon icon="${icon}"></ha-icon>
            <span class="stat-text">
              <span class="stat-value">${value != null ? `${Math.round(value)}%` : "–"}</span>
              <span class="stat-label">${label}</span>
            </span>
          </button>`;
      });
    return stats.length ? `<div class="stats">${stats.join("")}</div>` : "";
  }

  _renderControls(status, light) {
    const buttons = [];
    const active = status === "printing" || status === "paused";
    const offline = status === "offline";
    if (status === "printing" && this._eid("button.pause_print")) {
      buttons.push(this._control("pause", "mdi:pause", "Pause"));
    }
    if (status === "paused" && this._eid("button.resume_print")) {
      buttons.push(this._control("resume", "mdi:play", "Resume", "primary"));
    }
    if (active && this._eid("button.cancel_print")) {
      buttons.push(this._control("cancel", "mdi:stop", "Cancel", "danger", true));
    }
    if (light) {
      buttons.push(
        this._control("light", light.state === "on" ? "mdi:lightbulb-on" : "mdi:lightbulb-outline", "Light", light.state === "on" ? "on" : "", false, offline)
      );
    }
    for (const role of this._roles("switch.pin_")) {
      const filter = this._state(role);
      if (!filter) continue;
      const on = filter.state === "on";
      buttons.push(
        this._control(`toggle:${role}`, on ? "mdi:air-filter" : "mdi:air-purifier-off", "Filter", on ? "on" : "", false, offline)
      );
    }
    const bar = this._state("light.popstatus_light");
    if (bar && !bar.attributes.restored) {
      const on = bar.state === "on";
      // Opens the PopStatus panel (on/off, brightness, mode) under the controls
      buttons.push(
        this._control("pop-panel", on ? "mdi:led-strip-variant" : "mdi:led-strip-variant-off", "Status bar", `${on ? "on" : ""} ${this._popOpen ? "open" : ""}`.trim())
      );
    }
    if (this._printer?.screen_url) {
      buttons.push(this._control("screen", "mdi:cellphone-screenshot", "Screen", "", false, offline));
    }
    if (!active && this._eid("button.home_all")) {
      buttons.push(this._control("home", "mdi:home-import-outline", "Home", "", false, offline));
    }
    if (this._eid("button.emergency_stop")) {
      buttons.push(this._control("estop", "mdi:alert-octagon", "E-Stop", "danger", true, offline));
    }
    return `<div class="controls">${buttons.join("")}</div>`;
  }

  // BIQU PopStatus controls, shown when the Status bar button is tapped.
  _renderPopStatus() {
    const lightId = this._eid("light.popstatus_light");
    const bar = lightId && this._hass.states[lightId];
    if (!bar) return "";
    const offline = bar.state === "unavailable";
    const on = bar.state === "on";
    const pct = bar.attributes.brightness != null ? Math.round((bar.attributes.brightness * 100) / 255) : 0;
    const modeId = this._eid("select.popstatus_light_mode");
    const mode = modeId && this._hass.states[modeId];
    const advanced = mode?.state === "advanced";
    const link = this._state("binary_sensor.popstatus_printer_link");
    const linkText = offline
      ? "Light bar offline"
      : link?.state === "on"
        ? "Connected to printer"
        : link?.state === "off"
          ? "Not connected to printer"
          : "";
    const chip = (action, attrs, label, icon, active, disabled) => `
      <button class="preset ${active ? "active" : ""}" data-action="${action}" ${attrs} ${disabled ? "disabled" : ""}>
        <ha-icon icon="${icon}"></ha-icon><span class="preset-temp">${label}</span>
      </button>`;
    return `
      <div class="presets pop">
        <div class="presets-title">
          <ha-icon icon="mdi:led-strip-variant"></ha-icon>PopStatus
          <span class="pop-link ${link?.state === "on" && !offline ? "ok" : ""}">${esc(linkText)}</span>
        </div>
        <div class="preset-row">
          ${chip("pop-power", 'data-on="1"', "On", "mdi:power", on, offline)}
          ${chip("pop-power", 'data-on="0"', "Off", "mdi:power-off", !on && !offline, offline)}
        </div>
        <label class="pop-slider ${advanced ? "muted" : ""}" title="${advanced ? "In Advanced mode each effect has its own brightness (PopStatus web page)" : "Brightness"}">
          <ha-icon icon="mdi:brightness-6"></ha-icon>
          <input type="range" min="1" max="100" step="1" value="${pct || 1}" data-action="pop-brightness"
            data-entity="${esc(lightId)}" ${offline ? "disabled" : ""}>
          <span>${on ? `${pct}%` : "Off"}</span>
        </label>
        ${mode ? `<div class="preset-row">
          ${chip("pop-mode", `data-entity="${esc(modeId)}" data-option="simple"`, "Simple", "mdi:led-strip", mode.state === "simple", offline)}
          ${chip("pop-mode", `data-entity="${esc(modeId)}" data-option="advanced"`, "Advanced", "mdi:auto-fix", advanced, offline)}
        </div>` : ""}
        <div class="pop-links">
          <button class="pop-more" data-action="more-info" data-entity="${esc(lightId)}">
            <ha-icon icon="mdi:tune-variant"></ha-icon>More settings
          </button>
          ${bar.attributes.web_ui ? `<a class="pop-more" href="${esc(bar.attributes.web_ui)}" target="_blank" rel="noopener">
            <ha-icon icon="mdi:open-in-new"></ha-icon>Effects &amp; colours</a>` : ""}
        </div>
      </div>`;
  }

  _onChange(ev) {
    const target = ev.composedPath().find((el) => el.dataset && el.dataset.action);
    if (target?.dataset.action === "pop-brightness") {
      this._hass.callService("light", "turn_on", {
        entity_id: target.dataset.entity,
        brightness_pct: Number(target.value),
      });
    }
  }

  _control(action, icon, label, variant = "", confirm = false, disabled = false) {
    const armed = this._armed?.action === action;
    return `
      <button class="control ${variant} ${armed ? "armed" : ""}" data-action="${action}"
        ${confirm ? 'data-confirm="1"' : ""} ${disabled ? "disabled" : ""}>
        <ha-icon icon="${armed ? "mdi:alert" : icon}"></ha-icon>
        <span>${armed ? "Tap again" : label}</span>
      </button>`;
  }

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  _onClick(ev) {
    const target = ev.composedPath().find((el) => el.dataset && el.dataset.action);
    if (!target || target.disabled) return;
    const action = target.dataset.action;

    if (action === "camera") {
      this._moreInfo(this._eid("camera.webcam"));
      return;
    }
    if (action === "more-info") {
      this._moreInfo(target.dataset.entity);
      return;
    }

    // Destructive actions need a second tap within a few seconds.
    if (target.dataset.confirm && this._armed?.action !== action) {
      clearTimeout(this._armed?.timer);
      this._armed = {
        action,
        timer: setTimeout(() => {
          this._armed = null;
          this._safeUpdate();
        }, CONFIRM_MS),
      };
      this._safeUpdate();
      return;
    }
    clearTimeout(this._armed?.timer);
    this._armed = null;

    const press = (role) =>
      this._hass.callService("button", "press", { entity_id: this._eid(role) });
    switch (action) {
      case "pause":
        press("button.pause_print");
        break;
      case "resume":
        press("button.resume_print");
        break;
      case "cancel":
        press("button.cancel_print");
        break;
      case "home":
        press("button.home_all");
        break;
      case "pop-panel":
        this._popOpen = !this._popOpen;
        break;
      case "pop-power":
        this._hass.callService("light", target.dataset.on === "1" ? "turn_on" : "turn_off", {
          entity_id: this._eid("light.popstatus_light"),
        });
        break;
      case "pop-mode":
        this._hass.callService("select", "select_option", {
          entity_id: target.dataset.entity,
          option: target.dataset.option,
        });
        break;
      case "screen":
        // The printer's touchscreen in a new tab (Extended Firmware remote screen)
        window.open(this._printer.screen_url, "_blank", "noopener");
        break;
      case "estop":
        press("button.emergency_stop");
        break;
      default:
        if (action.startsWith("toggle:")) {
          const role = action.slice(7);
          this._hass.callService(role.split(".")[0], "toggle", { entity_id: this._eid(role) });
        }
        break;
      case "cover-mode":
        this._hass.callService("select", "select_option", {
          entity_id: target.dataset.entity,
          option: target.dataset.option,
        });
        break;
      case "preset":
        this._hass.callService("number", "set_value", {
          entity_id: target.dataset.entity,
          value: Number(target.dataset.temp),
        });
        break;
      case "light":
        this._hass.callService("switch", "toggle", { entity_id: this._eid("switch.work_light") });
        break;
    }
    this._safeUpdate();
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true })
    );
  }
}

class SnapmakerU1CardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) =>
        ({
          entry_id: "Printer",
          title: "Title (optional)",
          accent_color: "Accent color",
          show_camera: "Show camera",
          camera_mode: "Camera mode",
          show_controls: "Show controls",
          show_chamber_presets: "Show chamber heater presets",
          show_top_cover: "Show Top Cover controls",
        })[schema.name] || schema.name;
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(
          new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true })
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "entry_id", selector: { config_entry: { integration: DOMAIN } } },
      { name: "title", selector: { text: {} } },
      {
        name: "accent_color",
        selector: {
          select: {
            mode: "dropdown",
            custom_value: true,
            options: Object.keys(ACCENTS).map((key) => ({
              value: key,
              label: key === "theme" ? "Theme color" : key[0].toUpperCase() + key.slice(1),
            })),
          },
        },
      },
      { name: "show_camera", selector: { boolean: {} } },
      {
        name: "camera_mode",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "live", label: "Live video" },
              { value: "snapshot", label: "Snapshot every 5 s" },
            ],
          },
        },
      },
      { name: "show_controls", selector: { boolean: {} } },
      { name: "show_chamber_presets", selector: { boolean: {} } },
      { name: "show_top_cover", selector: { boolean: {} } },
    ];
    this._form.data = { ...SnapmakerU1Card.getStubConfig(), ...this._config };
  }
}

const STYLES = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  button { font: inherit; color: inherit; }
  .camera { position: relative; background: #000; cursor: pointer; aspect-ratio: 16 / 9; }
  .camera[hidden] { display: none; }
  .camera img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .camera-error {
    display: none; position: absolute; inset: 0; align-items: center; justify-content: center; gap: 8px;
    color: rgba(255,255,255,.75); font-size: 14px; --mdc-icon-size: 22px;
  }
  .camera.is-error img, .camera.is-error .camera-badge { visibility: hidden; }
  .camera.is-error .camera-error { display: flex; }
  .camera-badge {
    position: absolute; top: 10px; left: 10px; display: flex; align-items: center; gap: 6px;
    padding: 3px 8px; border-radius: 999px; background: rgba(0,0,0,.55); color: #fff;
    font-size: 11px; font-weight: 600; letter-spacing: .06em;
  }
  .camera-badge .dot { width: 7px; height: 7px; border-radius: 50%; background: #aaa; }
  .camera.is-live .camera-badge .dot { background: #ff4d4f; animation: pulse 1.6s infinite; }
  @keyframes pulse { 50% { opacity: .35; } }
  .content { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
  .empty { padding: 8px 0; color: var(--secondary-text-color); }
  .header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { display: flex; align-items: center; gap: 8px; font-size: 18px; font-weight: 600; min-width: 0; }
  .title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .title ha-icon { color: var(--sm-accent); }
  .pill {
    display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 999px;
    font-size: 13px; font-weight: 600; white-space: nowrap; --mdc-icon-size: 16px;
  }
  .tone-accent { color: var(--sm-accent); background: color-mix(in srgb, var(--sm-accent) 16%, transparent); }
  .tone-warning { color: var(--warning-color, #ffa600); background: color-mix(in srgb, var(--warning-color, #ffa600) 16%, transparent); }
  .tone-success { color: var(--success-color, #43a047); background: color-mix(in srgb, var(--success-color, #43a047) 16%, transparent); }
  .tone-error { color: var(--error-color, #db4437); background: color-mix(in srgb, var(--error-color, #db4437) 16%, transparent); }
  .tone-muted { color: var(--secondary-text-color); background: color-mix(in srgb, var(--secondary-text-color) 14%, transparent); }
  .alert {
    display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: 12px;
    color: var(--error-color, #db4437); background: color-mix(in srgb, var(--error-color, #db4437) 12%, transparent);
    font-size: 14px; --mdc-icon-size: 20px;
  }
  .progress { display: flex; align-items: center; gap: 16px; }
  .ring { position: relative; width: 104px; height: 104px; flex: none; }
  .ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
  .ring circle { fill: none; stroke-width: 9; }
  .ring .track { stroke: color-mix(in srgb, var(--sm-accent) 15%, transparent); }
  .ring .bar { stroke: var(--sm-accent); stroke-linecap: round; transition: stroke-dashoffset .6s ease; }
  .ring.paused .bar { stroke: var(--warning-color, #ffa600); }
  .ring-label { position: absolute; inset: 0; display: flex; align-items: baseline; justify-content: center; padding-top: 34px; }
  .ring-label .pct { font-size: 28px; font-weight: 700; }
  .ring-label .unit { font-size: 14px; color: var(--secondary-text-color); margin-left: 1px; }
  .job { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
  .filename { font-weight: 600; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .facts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
  .fact { display: flex; gap: 6px; align-items: center; min-width: 0; --mdc-icon-size: 18px; }
  .fact ha-icon { color: var(--sm-accent); flex: none; }
  .fact-label { font-size: 11px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .fact-value { font-size: 14px; font-weight: 600; white-space: nowrap; }
  .idle { display: flex; align-items: center; gap: 10px; color: var(--secondary-text-color); font-size: 15px; --mdc-icon-size: 22px; }
  .temps { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
  .temp {
    text-align: left; border: 1px solid var(--divider-color); border-radius: 14px; padding: 10px;
    background: var(--secondary-background-color, transparent); cursor: pointer; display: flex; flex-direction: column; gap: 2px;
  }
  .temp.active { border-color: var(--sm-accent); box-shadow: inset 0 0 0 1px var(--sm-accent); }
  .temp-head { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--secondary-text-color); --mdc-icon-size: 16px; }
  .temp-label { min-width: 0; overflow-wrap: anywhere; line-height: 1.25; }
  .temp.heating .temp-head ha-icon { color: var(--state-climate-heat-color, #ff8100); }
  .temp-value { font-size: 20px; font-weight: 700; }
  .temp-value span { font-size: 12px; font-weight: 500; color: var(--secondary-text-color); margin-left: 1px; }
  .temp-target { font-size: 12px; color: var(--secondary-text-color); }
  .swatch {
    display: inline-block; box-sizing: border-box; margin-left: auto; align-self: center;
    flex: 0 0 14px; width: 14px; height: 14px; border-radius: 50%;
    box-shadow: 0 0 0 1.5px color-mix(in srgb, var(--primary-text-color) 30%, transparent);
  }
  .swatch.is-empty { background: transparent; box-shadow: none; border: 1.5px dashed var(--secondary-text-color); }
  .temp-filament {
    margin-top: 2px; font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .temp-filament.is-empty { font-weight: 400; color: var(--secondary-text-color); }
  .temp.heating .temp-target { color: var(--state-climate-heat-color, #ff8100); }
  .presets { display: flex; flex-direction: column; gap: 6px; }
  .presets-title { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--secondary-text-color); --mdc-icon-size: 16px; }
  .presets-title ha-icon { color: var(--state-climate-heat-color, #ff8100); }
  .preset-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(38px, 1fr)); gap: 5px; }
  .preset {
    position: relative; display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 6px 0; min-width: 0;
    border-radius: 10px; border: 1px solid var(--divider-color); background: transparent; cursor: pointer; line-height: 1.15;
  }
  .preset:disabled { opacity: .4; cursor: default; }
  .preset-name { font-size: 12px; font-weight: 600; }
  .cover .preset-row { grid-template-columns: repeat(auto-fit, minmax(72px, 1fr)); }
  .pop .preset-row { grid-template-columns: repeat(auto-fit, minmax(72px, 1fr)); }
  .pop .preset { --mdc-icon-size: 18px; }
  .pop .preset.active { border-color: var(--sm-accent); background: color-mix(in srgb, var(--sm-accent) 14%, transparent); }
  .pop .preset.active .preset-temp, .pop .preset.active ha-icon { color: var(--sm-accent); }
  .pop .presets-title ha-icon { color: var(--sm-accent); }
  .pop-link { margin-left: auto; }
  .pop-link.ok { color: var(--success-color, #43a047); }
  .pop-slider { display: flex; align-items: center; gap: 8px; font-size: 13px; --mdc-icon-size: 18px; }
  .pop-slider input { flex: 1; accent-color: var(--sm-accent); }
  .pop-slider span { min-width: 34px; text-align: right; color: var(--secondary-text-color); }
  .pop-slider.muted { opacity: .55; }
  .pop-links { display: flex; gap: 8px; flex-wrap: wrap; }
  .pop-more {
    display: inline-flex; align-items: center; gap: 4px; font: inherit; font-size: 12px; cursor: pointer;
    color: var(--primary-color); background: none; border: none; padding: 2px 0; text-decoration: none; --mdc-icon-size: 16px;
  }
  .control.open { box-shadow: inset 0 0 0 2px var(--sm-accent); }
  .cover .preset { --mdc-icon-size: 18px; }
  .cover .preset.active { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 14%, transparent); }
  .cover .preset.active .preset-temp, .cover .preset.active ha-icon { color: var(--primary-color); }
  .cover .presets-title ha-icon { color: var(--state-fan-active-color, var(--primary-color)); }
  .cover-filter {
    margin-left: auto; display: inline-flex; align-items: center; gap: 4px;
    border: none; background: none; padding: 0; font: inherit; color: inherit; cursor: pointer;
  }
  .cover-filter.low { color: var(--error-color, #db4437); }
  .cover-filter.low ha-icon { color: var(--error-color, #db4437); }
  .preset-temp { font-size: 11px; color: var(--secondary-text-color); }
  .preset.active { border-color: var(--state-climate-heat-color, #ff8100); background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 14%, transparent); }
  .preset.active .preset-temp { color: var(--state-climate-heat-color, #ff8100); font-weight: 600; }
  .preset.active-off { border-color: var(--secondary-text-color); background: color-mix(in srgb, var(--primary-text-color) 7%, transparent); }
  .preset.active-off .preset-temp { color: var(--primary-text-color); font-weight: 600; }
  .preset.loaded::after {
    content: ""; position: absolute; top: 3px; right: 3px; width: 5px; height: 5px; border-radius: 50%; background: var(--sm-accent);
  }
  .stats { display: flex; gap: 8px; flex-wrap: wrap; }
  .stat {
    flex: 1; min-width: 72px; display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 12px;
    overflow: hidden;
    border: 1px solid var(--divider-color); background: transparent; cursor: pointer; --mdc-icon-size: 18px;
  }
  .stat ha-icon { color: var(--sm-accent); }
  .stat-text { display: flex; flex-direction: column; min-width: 0; text-align: left; line-height: 1.2; }
  .stat-value { font-weight: 600; font-size: 14px; }
  .stat-label { font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .stat ha-icon { flex: none; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; }
  .control {
    flex: 1; min-width: 58px; display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px 6px;
    border-radius: 14px; border: none; cursor: pointer; font-size: 12px; font-weight: 600;
    background: color-mix(in srgb, var(--primary-text-color) 7%, transparent); transition: background .15s, transform .1s;
  }
  .control:active { transform: scale(.97); }
  .control:disabled { opacity: .4; cursor: default; }
  .control.primary, .control.on { color: var(--sm-accent); background: color-mix(in srgb, var(--sm-accent) 16%, transparent); }
  .control.danger { color: var(--error-color, #db4437); }
  .control.armed { color: #fff; background: var(--error-color, #db4437); }
  @media (max-width: 420px) {
    .progress { flex-direction: column; align-items: stretch; }
    .ring { align-self: center; }
  }
`;

if (!customElements.get("snapmaker-u1-card")) {
  customElements.define("snapmaker-u1-card", SnapmakerU1Card);
  customElements.define("snapmaker-u1-card-editor", SnapmakerU1CardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "snapmaker-u1-card",
    name: "Snapmaker U1",
    description: "Printer dashboard: live camera, progress, time left, temperatures, Panda Breath and controls.",
    preview: true,
    documentationURL: "https://github.com/kbaker827/ha-snapmaker-u1#dashboard-card",
  });
  console.info(`%c SNAPMAKER-U1-CARD %c ${CARD_VERSION} `, "color:#fff;background:#ff8a00;font-weight:700", "");
}
