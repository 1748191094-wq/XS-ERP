from __future__ import annotations

import pytest

from app.integrations.calibration.duml import DumlCodecError, DumlPacket
from app.integrations.calibration.gimbal_engine import (
    CalibrationKind,
    GimbalCalibrationEngine,
    LiveCalibrationDisabled,
)
from app.integrations.calibration.transport import MemoryTransport


def test_joint_coarse_packet_matches_known_protocol_vector():
    engine = GimbalCalibrationEngine.for_profile("wm100")
    raw = engine.build_request(CalibrationKind.JOINT_COARSE).encode()
    assert raw.hex(" ").upper() == "55 0E 04 66 0A 04 00 00 40 04 08 01 BE B2"
    assert DumlPacket.decode(raw) == engine.build_request(CalibrationKind.JOINT_COARSE)


def test_linear_hall_packet_matches_known_protocol_vector():
    engine = GimbalCalibrationEngine.for_profile("wm100")
    raw = engine.build_request(CalibrationKind.LINEAR_HALL).encode()
    assert raw.hex(" ").upper() == "55 0E 04 66 0A 04 00 00 40 04 08 02 25 80"


@pytest.mark.parametrize("position", [3, 8, 11, 13])
def test_packet_decoder_rejects_corruption(position: int):
    raw = bytearray(
        GimbalCalibrationEngine.for_profile("wm100")
        .build_request(CalibrationKind.JOINT_COARSE)
        .encode()
    )
    raw[position] ^= 0x01
    with pytest.raises(DumlCodecError):
        DumlPacket.decode(bytes(raw))


def test_simulation_never_performs_hardware_io_and_live_path_is_closed():
    engine = GimbalCalibrationEngine.for_profile("wm230")
    result = engine.simulate(CalibrationKind.JOINT_COARSE)
    assert result["mode"] == "simulation"
    assert result["hardware_io_performed"] is False
    assert result["live_execution_available"] is False
    assert result["events"][-1]["state"] == "completed"
    assert result["request_round_trip_valid"] is True
    with pytest.raises(LiveCalibrationDisabled):
        engine.run_live(CalibrationKind.JOINT_COARSE, MemoryTransport())
