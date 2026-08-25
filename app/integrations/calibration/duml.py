from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


SOF = 0x55
PROTOCOL_VERSION = 1
HEADER_LENGTH = 11
CHECKSUM_LENGTH = 2
MAX_PACKET_LENGTH = 0x03FF


class DumlCodecError(ValueError):
    """Raised when a DUML v1 packet cannot be safely encoded or decoded."""


class ModuleType(IntEnum):
    GIMBAL = 4
    PC = 10


class AckType(IntEnum):
    NONE = 0
    BEFORE_EXECUTION = 1
    AFTER_EXECUTION = 2


class PacketType(IntEnum):
    REQUEST = 0
    RESPONSE = 1


class CommandSet(IntEnum):
    ZENMUSE = 4


class GimbalCommand(IntEnum):
    CALIBRATION = 0x08


def crc8(data: bytes, seed: int = 0x77) -> int:
    """Compute DJI DUML header CRC-8 without embedding a copied lookup table."""
    crc = seed & 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8C) if crc & 1 else crc >> 1
    return crc & 0xFF


def crc16(data: bytes, seed: int = 0x3692) -> int:
    """Compute DJI DUML packet CRC-16 without embedding a copied lookup table."""
    crc = seed & 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8408) if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _pack_address(module_type: int, module_index: int) -> int:
    if not 0 <= int(module_type) <= 0x1F:
        raise DumlCodecError("module_type must fit in 5 bits")
    if not 0 <= module_index <= 0x07:
        raise DumlCodecError("module_index must fit in 3 bits")
    return (module_index << 5) | int(module_type)


@dataclass(frozen=True, slots=True)
class DumlPacket:
    sender_type: int
    receiver_type: int
    command_set: int
    command_id: int
    payload: bytes = b""
    sequence: int = 0
    sender_index: int = 0
    receiver_index: int = 0
    packet_type: PacketType = PacketType.REQUEST
    ack_type: AckType = AckType.AFTER_EXECUTION
    encryption_type: int = 0
    protocol_version: int = PROTOCOL_VERSION

    def encode(self) -> bytes:
        if not 0 <= self.sequence <= 0xFFFF:
            raise DumlCodecError("sequence must fit in 16 bits")
        if not 0 <= self.command_set <= 0xFF or not 0 <= self.command_id <= 0xFF:
            raise DumlCodecError("command fields must fit in one byte")
        if not 0 <= self.encryption_type <= 0x07:
            raise DumlCodecError("encryption_type must fit in 3 bits")
        total_length = HEADER_LENGTH + len(self.payload) + CHECKSUM_LENGTH
        if total_length > MAX_PACKET_LENGTH:
            raise DumlCodecError("packet exceeds DUML v1 maximum length")

        version_length = (self.protocol_version << 10) | total_length
        header = bytearray((SOF, version_length & 0xFF, version_length >> 8))
        header.append(crc8(bytes(header)))
        header.append(_pack_address(self.sender_type, self.sender_index))
        header.append(_pack_address(self.receiver_type, self.receiver_index))
        header.extend(self.sequence.to_bytes(2, "little"))
        command_type = (
            (int(self.packet_type) << 7)
            | (int(self.ack_type) << 5)
            | self.encryption_type
        )
        header.extend((command_type, self.command_set, self.command_id))
        body = bytes(header) + bytes(self.payload)
        return body + crc16(body).to_bytes(2, "little")

    @classmethod
    def decode(cls, raw: bytes) -> "DumlPacket":
        if len(raw) < HEADER_LENGTH + CHECKSUM_LENGTH:
            raise DumlCodecError("packet is shorter than the minimum DUML frame")
        if raw[0] != SOF:
            raise DumlCodecError("invalid DUML start-of-frame byte")
        version_length = int.from_bytes(raw[1:3], "little")
        total_length = version_length & 0x03FF
        protocol_version = version_length >> 10
        if total_length != len(raw):
            raise DumlCodecError("packet length field does not match received bytes")
        if protocol_version != PROTOCOL_VERSION:
            raise DumlCodecError(f"unsupported DUML protocol version: {protocol_version}")
        if crc8(raw[:3]) != raw[3]:
            raise DumlCodecError("DUML header CRC-8 mismatch")
        expected_crc = int.from_bytes(raw[-2:], "little")
        if crc16(raw[:-2]) != expected_crc:
            raise DumlCodecError("DUML packet CRC-16 mismatch")

        sender, receiver, command_type = raw[4], raw[5], raw[8]
        try:
            packet_type = PacketType((command_type >> 7) & 0x01)
            ack_type = AckType((command_type >> 5) & 0x03)
        except ValueError as exc:
            raise DumlCodecError("unsupported DUML packet or acknowledgement type") from exc
        return cls(
            sender_type=sender & 0x1F,
            sender_index=sender >> 5,
            receiver_type=receiver & 0x1F,
            receiver_index=receiver >> 5,
            sequence=int.from_bytes(raw[6:8], "little"),
            packet_type=packet_type,
            ack_type=ack_type,
            encryption_type=command_type & 0x07,
            command_set=raw[9],
            command_id=raw[10],
            payload=bytes(raw[11:-2]),
            protocol_version=protocol_version,
        )


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)
