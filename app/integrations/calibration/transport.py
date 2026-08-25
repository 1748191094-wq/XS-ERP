from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CalibrationTransport(Protocol):
    """Narrow transport surface used by the calibration state machine."""

    def open(self) -> None: ...
    def write(self, packet: bytes) -> None: ...
    def read(self, timeout_seconds: float) -> bytes: ...
    def close(self) -> None: ...


class MemoryTransport:
    """Deterministic transport for simulation and tests; never touches hardware."""

    def __init__(self, responses: list[bytes] | None = None) -> None:
        self.responses = list(responses or [])
        self.writes: list[bytes] = []
        self.is_open = False

    def open(self) -> None:
        self.is_open = True

    def write(self, packet: bytes) -> None:
        if not self.is_open:
            raise RuntimeError("transport is not open")
        self.writes.append(bytes(packet))

    def read(self, timeout_seconds: float) -> bytes:
        del timeout_seconds
        if not self.is_open:
            raise RuntimeError("transport is not open")
        if not self.responses:
            raise TimeoutError("simulated device has no queued response")
        return self.responses.pop(0)

    def close(self) -> None:
        self.is_open = False


@dataclass(frozen=True, slots=True)
class SerialPortDescriptor:
    device: str
    description: str
    hardware_id: str
    vendor_id: int | None
    product_id: int | None

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "description": self.description,
            "hardware_id": self.hardware_id,
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
        }


def list_serial_ports() -> dict:
    """Read-only discovery. Importing pyserial does not open any device."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return {
            "available": False,
            "reason": "pyserial_not_installed",
            "ports": [],
        }
    ports = [
        SerialPortDescriptor(
            device=port.device,
            description=port.description or "",
            hardware_id=port.hwid or "",
            vendor_id=port.vid,
            product_id=port.pid,
        ).to_dict()
        for port in list_ports.comports()
    ]
    return {"available": True, "reason": None, "ports": ports}
