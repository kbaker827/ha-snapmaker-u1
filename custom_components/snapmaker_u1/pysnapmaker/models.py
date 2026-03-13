"""Data models for the Snapmaker U1 (Moonraker/Klipper) printer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .const import KLIPPER_STARTUP, STATE_STANDBY


@dataclass
class ExtruderData:
    """Data for a single extruder/nozzle."""

    temperature: float = 0.0
    target: float = 0.0
    power: float = 0.0
    can_extrude: bool = False


@dataclass
class HeaterBedData:
    """Data for the heated bed."""

    temperature: float = 0.0
    target: float = 0.0
    power: float = 0.0


@dataclass
class PrintStats:
    """Current print job statistics."""

    state: str = STATE_STANDBY
    filename: str = ""
    total_duration: float = 0.0
    print_duration: float = 0.0
    filament_used: float = 0.0
    current_layer: int = 0
    total_layer: int = 0


@dataclass
class VirtualSdCard:
    """Virtual SD card state (file progress)."""

    progress: float = 0.0
    is_active: bool = False
    file_position: int = 0
    file_size: int = 0


@dataclass
class Toolhead:
    """Toolhead position and state."""

    position: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    homed_axes: str = ""
    max_velocity: float = 0.0
    max_accel: float = 0.0


@dataclass
class FanData:
    """Part cooling fan data."""

    speed: float = 0.0


@dataclass
class IdleTimeout:
    """Idle timeout state."""

    state: str = "Idle"
    printing_time: float = 0.0


@dataclass
class GcodeMove:
    """Live G-code motion state (includes override factors)."""

    speed_factor: float = 1.0    # M220: 1.0 = 100 %
    extrude_factor: float = 1.0  # M221: 1.0 = 100 %


@dataclass
class FilamentSensor:
    """State of a single filament runout sensor."""

    enabled: bool = True
    filament_detected: bool = True   # True = filament present


@dataclass
class SnapmakerPrinterData:
    """Complete snapshot of Snapmaker U1 printer state."""

    # Klipper daemon state
    klipper_state: str = KLIPPER_STARTUP
    klipper_message: str = ""

    # Print job
    print_stats: PrintStats = field(default_factory=PrintStats)
    virtual_sdcard: VirtualSdCard = field(default_factory=VirtualSdCard)

    # Temperatures
    extruders: dict[str, ExtruderData] = field(default_factory=dict)
    heater_bed: HeaterBedData = field(default_factory=HeaterBedData)
    # Extra temperature sensors (e.g. "chamber", "mcu")
    chamber_sensors: dict[str, float] = field(default_factory=dict)

    # Motion / overrides
    toolhead: Toolhead = field(default_factory=Toolhead)
    gcode_move: GcodeMove = field(default_factory=GcodeMove)

    # Ancillary
    fan: FanData = field(default_factory=FanData)
    idle_timeout: IdleTimeout = field(default_factory=IdleTimeout)
    display_message: str = ""

    # Filament sensors  {object_key: FilamentSensor}
    filament_sensors: dict[str, FilamentSensor] = field(default_factory=dict)

    # File management
    available_files: list[str] = field(default_factory=list)

    # Hardware counts
    extruder_count: int = 0

    # Printer identity (from /printer/info)
    printer_name: str = "Snapmaker U1"
    firmware_version: str = ""

    # Work-light optimistic state
    work_light_on: bool = False

    # -------------------------------------------------------------------
    # Computed properties
    # -------------------------------------------------------------------

    @property
    def is_printing(self) -> bool:
        return self.print_stats.state == "printing"

    @property
    def is_paused(self) -> bool:
        return self.print_stats.state == "paused"

    @property
    def is_ready(self) -> bool:
        return self.klipper_state == "ready"

    @property
    def has_error(self) -> bool:
        return self.print_stats.state == "error" or self.klipper_state == "error"

    @property
    def print_progress_pct(self) -> float:
        """Print progress as a percentage (0–100)."""
        return round(self.virtual_sdcard.progress * 100, 1)

    @property
    def time_remaining(self) -> Optional[int]:
        """Estimated seconds remaining, or None when not calculable."""
        if not self.is_printing:
            return None
        progress = self.virtual_sdcard.progress
        elapsed = self.print_stats.print_duration
        if progress <= 0 or elapsed <= 0:
            return None
        if progress >= 1.0:
            return 0
        total_estimated = elapsed / progress
        return max(0, int(total_estimated - elapsed))

    @property
    def speed_factor_pct(self) -> int:
        """Print speed override as a percentage (e.g. 100)."""
        return round(self.gcode_move.speed_factor * 100)

    @property
    def flow_rate_pct(self) -> int:
        """Extrusion flow-rate override as a percentage (e.g. 100)."""
        return round(self.gcode_move.extrude_factor * 100)

    @property
    def primary_extruder(self) -> Optional[ExtruderData]:
        return self.extruders.get("extruder")
