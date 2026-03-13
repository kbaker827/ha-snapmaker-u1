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
ENDPOINT_PRINT_START = "/printer/print/start"
ENDPOINT_WEBCAMS_LIST = "/server/webcams/list"

# HA event names fired on print-state transitions
EVENT_PRINT_STARTED = "snapmaker_u1_print_started"
EVENT_PRINT_COMPLETE = "snapmaker_u1_print_complete"
EVENT_PRINT_FAILED = "snapmaker_u1_print_failed"
EVENT_PRINT_PAUSED = "snapmaker_u1_print_paused"
EVENT_PRINT_CANCELLED = "snapmaker_u1_print_cancelled"

# Map Moonraker print state → HA event suffix (only states that trigger events)
PRINT_STATE_EVENTS: dict[str, str] = {
    "printing": EVENT_PRINT_STARTED,
    "complete": EVENT_PRINT_COMPLETE,
    "error": EVENT_PRINT_FAILED,
    "paused": EVENT_PRINT_PAUSED,
    "cancelled": EVENT_PRINT_CANCELLED,
}

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
