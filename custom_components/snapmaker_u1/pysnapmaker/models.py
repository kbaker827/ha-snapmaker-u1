"""Data models for the Snapmaker U1 (Moonraker/Klipper) printer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .const import STATE_STANDBY, KLIPPER_STARTUP


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

    # Motion
    toolhead: Toolhead = field(default_factory=Toolhead)

    # Ancillary
    fan: FanData = field(default_factory=FanData)
    idle_timeout: IdleTimeout = field(default_factory=IdleTimeout)
    display_message: str = ""

    # Detected hardware
    extruder_count: int = 0

    # Printer identity (from /printer/info)
    printer_name: str = "Snapmaker U1"
    firmware_version: str = ""
    host_stats_available: bool = False

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
    def primary_extruder(self) -> Optional[ExtruderData]:
        return self.extruders.get("extruder")
