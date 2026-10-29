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
ENTRY_TYPE_POPKNOMI = "popknomi"
ACCESSORY_ENTRY_TYPES = (ENTRY_TYPE_POPSTATUS, ENTRY_TYPE_POPKNOMI)
CONF_PRINTER_ENTRY_ID = "printer_entry_id"
# hass.data key: entry_id -> PopStatusClient / PopKnomiClient
DATA_POPSTATUS = f"{DOMAIN}_popstatus"

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


def is_popknomi_entry(entry) -> bool:
    """True for a PopKnomi status display entry."""
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_POPKNOMI


def is_accessory_entry(entry) -> bool:
    """True for a BIQU accessory entry (PopStatus, PopKnomi), not a printer."""
    return entry.data.get(CONF_ENTRY_TYPE) in ACCESSORY_ENTRY_TYPES
