from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings


class UnsupportedLogError(Exception):
    pass


@dataclass(slots=True)
class ParsedFlightLog:
    metadata: dict[str, Any]
    time_series: list[dict[str, Any]]
    events: list[dict[str, Any]]
    warnings: list[str]
    parser_name: str
    parser_version: str
    raw_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FlightLogParser(Protocol):
    name: str
    version: str
    def supports(self, file_path: str) -> bool: ...
    def parse(self, file_path: str) -> ParsedFlightLog: ...


def _looks_like_structured_text(file_path: str) -> bool:
    path = Path(file_path)
    if path.suffix.lower() == ".csv":
        return True
    sample = path.read_bytes()[:8192]
    if b"\x00" in sample:
        return False
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        first_line = text.splitlines()[0] if text.splitlines() else ""
        return sum(first_line.count(delimiter) for delimiter in (",", "\t", ";")) >= 1
    return False


class DJITextParser:
    name = "dji_text_csv"
    version = "1.0.0"

    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in {".csv", ".txt"} and _looks_like_structured_text(file_path)

    def parse(self, file_path: str) -> ParsedFlightLog:
        path = Path(file_path)
        last_error = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                with path.open("r", encoding=encoding, newline="") as stream:
                    reader = csv.DictReader(stream)
                    if not reader.fieldnames or len(reader.fieldnames) < 2:
                        raise UnsupportedLogError("文件不是结构化 CSV/第三方导出表")
                    rows = []
                    for index, row in enumerate(reader):
                        if index >= 20_000:
                            break
                        rows.append({str(k).strip(): v for k, v in row.items() if k})
                return ParsedFlightLog(
                    metadata={"filename": path.name, "columns": reader.fieldnames, "row_count": len(rows)},
                    time_series=rows, events=[], warnings=[], parser_name=self.name, parser_version=self.version,
                    raw_summary={"truncated": len(rows) >= 20_000},
                )
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
        raise UnsupportedLogError(f"无法按 UTF-8 或 GB18030 读取：{last_error}")


def _json_rows(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(payload, list):
        return ([row for row in payload if isinstance(row, dict)][:20_000], [])
    if not isinstance(payload, dict):
        return ([], [])
    if isinstance(payload.get("time_series"), list):
        rows = [row for row in payload["time_series"] if isinstance(row, dict)][:20_000]
        events = [event for event in payload.get("events", []) if isinstance(event, dict)][:2_000]
        return rows, events
    for key in ("frames", "records", "data", "flightData", "samples"):
        value = payload.get(key)
        if isinstance(value, list) and any(isinstance(row, dict) for row in value):
            return ([row for row in value if isinstance(row, dict)][:20_000], [])
    return ([], [])


class DJIFlightRecordV13Parser:
    """Adapter for DJI's official FlightRecordParsingLib wrapper executable.

    The upstream library is C++ and needs a DJI developer App Key. This service
    deliberately does not embed a key or attempt private-protocol decryption.
    """

    name = "dji_official_flight_record_v13"
    version = "external-1"

    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() == ".txt" and not _looks_like_structured_text(file_path)

    def parse(self, file_path: str) -> ParsedFlightLog:
        executable = Path(settings.dji_flight_record_parser_path) if settings.dji_flight_record_parser_path else None
        if not executable or not executable.is_file():
            raise UnsupportedLogError(
                "检测到 DJI 二进制 Flight Record TXT；需配置经本机验证的 DJI 官方 FlightRecordParsingLib v13 包装程序和开发者 App Key"
            )
        try:
            result = subprocess.run(
                [str(executable), str(Path(file_path).resolve())],
                check=False,
                capture_output=True,
                timeout=settings.dji_flight_record_parser_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise UnsupportedLogError("DJI 官方日志解析器执行超时") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-1000:]
            raise UnsupportedLogError(f"DJI 官方日志解析器返回错误码 {result.returncode}：{detail or '无错误详情'}")
        if len(result.stdout) > 100 * 1024 * 1024:
            raise UnsupportedLogError("DJI 官方日志解析结果超过 100MB 安全上限")
        try:
            payload = json.loads(result.stdout.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UnsupportedLogError("DJI 官方日志解析器未返回有效 UTF-8 JSON") from exc
        rows, events = _json_rows(payload)
        warnings = [] if rows else ["官方解析器已返回数据，但未识别到标准时间序列；仅保留原始摘要"]
        return ParsedFlightLog(
            metadata={"filename": Path(file_path).name, "format": "DJI Flight Record v13", "row_count": len(rows)},
            time_series=rows,
            events=events,
            warnings=warnings,
            parser_name=self.name,
            parser_version=self.version,
            raw_summary={"top_level_type": type(payload).__name__, "truncated": len(rows) >= 20_000},
        )


class _StubParser:
    version = "0.1.0-stub"
    suffixes: tuple[str, ...] = ()
    name = "stub"
    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self.suffixes
    def parse(self, file_path: str) -> ParsedFlightLog:
        raise UnsupportedLogError(f"{self.name} 第一阶段仅保留适配器接口，未伪造解析结果")


class DJIDatParserStub(_StubParser):
    name = "dji_dat_stub"
    suffixes = (".dat",)

    def parse(self, file_path: str) -> ParsedFlightLog:
        raise UnsupportedLogError(
            "DJI DAT 属于机载/飞控日志且型号差异较大；官方 FlightRecordParsingLib 不解析 DAT，当前不启用逆向工具自动判定"
        )


class PX4ULogParser:
    name = "px4_pyulog"
    version = "optional-1"

    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() == ".ulg"

    def parse(self, file_path: str) -> ParsedFlightLog:
        try:
            from pyulog import ULog
        except ImportError as exc:
            raise UnsupportedLogError("PX4 ULog 已识别；安装可选依赖 pyulog 后可进行本地解析") from exc
        ulog = ULog(file_path)
        rows: list[dict[str, Any]] = []
        topics: list[str] = []
        for dataset in ulog.data_list:
            topic = str(dataset.name)
            topics.append(topic)
            columns = dataset.data
            count = len(next(iter(columns.values()))) if columns else 0
            for index in range(min(count, 20_000 - len(rows))):
                row: dict[str, Any] = {"topic": topic}
                for key, values in columns.items():
                    value = values[index]
                    row[str(key)] = value.item() if hasattr(value, "item") else value
                rows.append(row)
            if len(rows) >= 20_000:
                break
        return ParsedFlightLog(
            metadata={"filename": Path(file_path).name, "topics": topics, "row_count": len(rows)},
            time_series=rows, events=[], warnings=[], parser_name=self.name, parser_version=self.version,
            raw_summary={"truncated": len(rows) >= 20_000},
        )


class ArduPilotBinParser:
    name = "ardupilot_pymavlink"
    version = "optional-1"

    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() == ".bin"

    def parse(self, file_path: str) -> ParsedFlightLog:
        try:
            from pymavlink import mavutil
        except ImportError as exc:
            raise UnsupportedLogError("ArduPilot BIN 已识别；安装可选依赖 pymavlink 后可进行本地解析") from exc
        connection = mavutil.mavlink_connection(file_path)
        rows: list[dict[str, Any]] = []
        message_types: set[str] = set()
        while len(rows) < 20_000:
            message = connection.recv_match(blocking=False)
            if message is None:
                break
            row = message.to_dict()
            message_types.add(str(row.get("mavpackettype", "unknown")))
            rows.append(row)
        return ParsedFlightLog(
            metadata={"filename": Path(file_path).name, "message_types": sorted(message_types), "row_count": len(rows)},
            time_series=rows, events=[], warnings=[], parser_name=self.name, parser_version=self.version,
            raw_summary={"truncated": len(rows) >= 20_000},
        )


class ParserRegistry:
    def __init__(self):
        self.parsers: list[FlightLogParser] = [
            DJITextParser(),
            DJIFlightRecordV13Parser(),
            DJIDatParserStub(),
            PX4ULogParser(),
            ArduPilotBinParser(),
        ]

    def parser_for(self, file_path: str) -> FlightLogParser:
        for parser in self.parsers:
            if parser.supports(file_path):
                return parser
        raise UnsupportedLogError("没有解析器支持该文件类型")
