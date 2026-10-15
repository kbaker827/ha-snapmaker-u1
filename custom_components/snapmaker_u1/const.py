"""Constants for the Snapmaker U1 Home Assistant integration."""

DOMAIN = "snapmaker_u1"
MANUFACTURER = "Snapmaker"
MODEL = "U1"
U1_EXTRUDER_COUNT = 4

CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TEMPERATURE_UNIT = "temperature_unit"

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
