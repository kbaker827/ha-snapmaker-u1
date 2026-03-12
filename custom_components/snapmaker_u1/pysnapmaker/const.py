"""Constants for the pysnapmaker Moonraker API client."""

# Moonraker HTTP endpoints
ENDPOINT_PRINTER_INFO = "/printer/info"
ENDPOINT_SERVER_INFO = "/server/info"
ENDPOINT_PRINTER_OBJECTS_QUERY = "/printer/objects/query"
ENDPOINT_PRINTER_OBJECTS_LIST = "/printer/objects/list"
ENDPOINT_PRINT_PAUSE = "/printer/print/pause"
ENDPOINT_PRINT_RESUME = "/printer/print/resume"
ENDPOINT_PRINT_CANCEL = "/printer/print/cancel"
ENDPOINT_EMERGENCY_STOP = "/printer/emergency_stop"
ENDPOINT_PRINTER_RESTART = "/printer/restart"
ENDPOINT_GCODE_SCRIPT = "/printer/gcode/script"
ENDPOINT_SERVER_TEMPERATURE_STORE = "/server/temperature_store"
ENDPOINT_FILES_LIST = "/server/files/list"

# WebSocket
WS_ENDPOINT = "/websocket"
WS_METHOD_SUBSCRIBE = "printer.objects.subscribe"
WS_NOTIFY_STATUS_UPDATE = "notify_status_update"
WS_NOTIFY_KLIPPY_READY = "notify_klippy_ready"
WS_NOTIFY_KLIPPY_SHUTDOWN = "notify_klippy_shutdown"
WS_NOTIFY_KLIPPY_DISCONNECTED = "notify_klippy_disconnected"
WS_NOTIFY_HISTORY_CHANGED = "notify_history_changed"

# Camera
CAMERA_STREAM_PATH = "/webcam/stream.mjpg"
CAMERA_SNAPSHOT_PATH = "/webcam/snapshot.jpg"

# Printer objects to subscribe/query
# None value means subscribe to all fields for that object
PRINTER_OBJECTS = [
    "print_stats",
    "virtual_sdcard",
    "extruder",
    "extruder1",
    "extruder2",
    "extruder3",
    "heater_bed",
    "toolhead",
    "display_status",
    "webhooks",
    "fan",
    "idle_timeout",
    "gcode_move",
]

# Print states (Moonraker/Klipper)
STATE_STANDBY = "standby"
STATE_PRINTING = "printing"
STATE_PAUSED = "paused"
STATE_COMPLETE = "complete"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

# Klipper states
KLIPPER_READY = "ready"
KLIPPER_STARTUP = "startup"
KLIPPER_SHUTDOWN = "shutdown"
KLIPPER_ERROR = "error"
