from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Any

from app.integrations.calibration.duml import (
    AckType,
    CommandSet,
    DumlPacket,
    GimbalCommand,
    ModuleType,
    PacketType,
    hex_bytes,
)
from app.integrations.calibration.profiles import CalibrationProfile, get_profile
from app.integrations.calibration.transport import CalibrationTransport, MemoryTransport


class CalibrationKind(str, Enum):
    JOINT_COARSE = "joint_coarse"
    LINEAR_HALL = "linear_hall"

    @property
    def command_value(self) -> int:
        return 1 if self is CalibrationKind.JOINT_COARSE else 2

    @property
    def simulated_success_payload(self) -> bytes:
        return b"\x10\x01" if self is CalibrationKind.JOINT_COARSE else b"\x28\x01"


class LiveCalibrationDisabled(RuntimeError):
    pass


@dataclass(slots=True)
class GimbalCalibrationEngine:
    profile: CalibrationProfile

    @classmethod
    def for_profile(cls, profile_id: str) -> "GimbalCalibrationEngine":
        return cls(profile=get_profile(profile_id))

    def build_request(
        self,
        kind: CalibrationKind,
        *,
        sequence: int = 0,
        ack_type: AckType = AckType.AFTER_EXECUTION,
    ) -> DumlPacket:
        if kind.value not in self.profile.calibration_kinds:
            raise ValueError(f"{kind.value} is not listed for {self.profile.profile_id}")
        return DumlPacket(
            sender_type=ModuleType.PC,
            receiver_type=ModuleType.GIMBAL,
            sequence=sequence,
            ack_type=ack_type,
            command_set=CommandSet.ZENMUSE,
            command_id=GimbalCommand.CALIBRATION,
            payload=bytes((kind.command_value,)),
        )

    def simulate(self, kind: CalibrationKind) -> dict[str, Any]:
        started = monotonic()
        request = self.build_request(kind)
        encoded = request.encode()
        response = DumlPacket(
            sender_type=ModuleType.GIMBAL,
            receiver_type=ModuleType.PC,
            sequence=request.sequence,
            packet_type=PacketType.RESPONSE,
            ack_type=AckType.NONE,
            command_set=CommandSet.ZENMUSE,
            command_id=GimbalCommand.CALIBRATION,
            payload=kind.simulated_success_payload,
        ).encode()
        transport = MemoryTransport([response])
        events: list[dict[str, Any]] = [
            {"state": "preflight", "progress": 0, "message": "模拟模式确认；未打开任何硬件端口。"},
            {"state": "packet_ready", "progress": 10, "message": "标定请求已编码并通过本地 CRC 校验。"},
        ]
        transport.open()
        try:
            transport.write(encoded)
            events.append({"state": "simulated_send", "progress": 25, "message": "请求仅写入内存模拟设备。"})
            events.append({"state": "calibrating", "progress": 72, "message": "模拟云台标定状态机运行中。"})
            decoded_response = DumlPacket.decode(transport.read(timeout_seconds=1.0))
        finally:
            transport.close()
        if decoded_response.payload != kind.simulated_success_payload:
            raise RuntimeError("simulator returned an unexpected completion payload")
        events.append({"state": "completed", "progress": 100, "message": "模拟应答校验通过；没有向真实设备发送数据。"})
        return {
            "mode": "simulation",
            "profile": self.profile.to_dict(),
            "calibration_kind": kind.value,
            "request_hex": hex_bytes(encoded),
            "response_hex": hex_bytes(response),
            "request_round_trip_valid": DumlPacket.decode(encoded) == request,
            "hardware_io_performed": False,
            "live_execution_available": False,
            "events": events,
            "elapsed_ms": round((monotonic() - started) * 1000, 3),
        }

    def run_live(self, kind: CalibrationKind, transport: CalibrationTransport) -> None:
        del kind, transport
        raise LiveCalibrationDisabled(
            "Real-device calibration is disabled until the maintainer validates the exact model, "
            "hardware revision, firmware, acknowledgement behavior, and recovery path."
        )
