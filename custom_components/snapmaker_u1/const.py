"""Constants for the Snapmaker U1 Home Assistant integration."""

DOMAIN = "snapmaker_u1"
MANUFACTURER = "Snapmaker"
MODEL = "U1"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 30  # seconds

# How long (seconds) to wait for the first coordinator refresh
COORDINATOR_TIMEOUT = 30
