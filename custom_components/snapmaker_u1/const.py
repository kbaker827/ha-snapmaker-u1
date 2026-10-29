"""Constants for the Snapmaker U1 Home Assistant integration."""

DOMAIN = "snapmaker_u1"
MANUFACTURER = "Snapmaker"
MODEL = "U1"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TEMPERATURE_UNIT = "temperature_unit"
# Config entries are printers unless entry_type says otherwise. A BIQU
# PopStatus light bar is its own entry, linked to a printer entry.
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_POPSTATUS = "popstatus"
CONF_PRINTER_ENTRY_ID = "printer_entry_id"
DATA_POPSTATUS = f"{DOMAIN}_popstatus"  # hass.data key: entry_id -> PopStatusClient

# Unit used for every printer temperature. 3D printing works in °C, so that is
# the default even when Home Assistant itself is set to °F.
TEMP_UNIT_CELSIUS = "celsius"
TEMP_UNIT_FAHRENHEIT = "fahrenheit"
TEMP_UNIT_SYSTEM = "system"  # follow Home Assistant's unit system
DEFAULT_TEMPERATURE_UNIT = TEMP_UNIT_CELSIUS

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 30  # seconds

# How long (seconds) to wait for the first coordinator refresh
COORDINATOR_TIMEOUT = 30


def is_popstatus_entry(entry) -> bool:
    """True for a PopStatus config entry (False for a printer)."""
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_POPSTATUS
