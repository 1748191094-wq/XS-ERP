from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


THRESHOLDS = {
    "battery_cell_delta_v": Decimal("0.20"),
    "battery_voltage_drop_v_per_sample": Decimal("1.50"),
    "gps_satellites_low": Decimal("8"),
    "motor_rpm_imbalance_ratio": Decimal("0.20"),
    "imu_attitude_error_deg": Decimal("10"),
    "compass_interference_level": Decimal("2"),
}


@dataclass(slots=True)
class Finding:
    diagnosis_type: str
    severity: str
    confidence: Decimal
    title: str
    description: str
    evidence: dict[str, Any]
    suggested_actions: str
    requires_human_confirmation: bool = True


class Analyzer(Protocol):
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]: ...


def _numbers(rows: list[dict[str, Any]], aliases: tuple[str, ...]) -> list[Decimal]:
    values: list[Decimal] = []
    lowered = {alias.lower() for alias in aliases}
    for row in rows:
        for key, value in row.items():
            if key.lower() in lowered and value not in (None, ""):
                try:
                    values.append(Decimal(str(value)))
                except Exception:
                    pass
    return values


class BatteryAnalyzer:
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        rows = parsed.get("time_series", [])
        deltas = _numbers(rows, ("cell_delta", "battery_cell_delta", "voltage_delta"))
        findings: list[Finding] = []
        if deltas and max(deltas) > THRESHOLDS["battery_cell_delta_v"]:
            peak = max(deltas)
            findings.append(Finding("battery", "high", Decimal("0.85"), "单体电芯压差过大", f"日志中最大电芯压差为 {peak}V。", {"max_cell_delta_v": str(peak)}, "复测电池内阻与单体电压，必要时停用该电池。"))
        voltages = _numbers(rows, ("voltage", "battery_voltage", "voltage_v"))
        drops = [voltages[i - 1] - voltages[i] for i in range(1, len(voltages))]
        if drops and max(drops) > THRESHOLDS["battery_voltage_drop_v_per_sample"]:
            peak_drop = max(drops)
            findings.append(Finding("battery", "high", Decimal("0.72"), "电池电压快速下跌", f"相邻采样最大压降为 {peak_drop}V。", {"max_drop_v": str(peak_drop)}, "检查电池负载能力、接插件和电源回路。"))
        return findings


class MotorAnalyzer:
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        ratios: list[Decimal] = []
        for row in parsed.get("time_series", []):
            rpms: list[Decimal] = []
            for key, value in row.items():
                key_lower = key.lower()
                if "motor" in key_lower and ("rpm" in key_lower or "speed" in key_lower):
                    try:
                        rpms.append(Decimal(str(value)))
                    except Exception:
                        pass
            positive = [value for value in rpms if value > 0]
            if len(positive) >= 2:
                average = sum(positive) / len(positive)
                ratios.append((max(positive) - min(positive)) / average)
        if ratios and sum(r > THRESHOLDS["motor_rpm_imbalance_ratio"] for r in ratios) / len(ratios) >= 0.3:
            peak = max(ratios)
            return [Finding("motor", "high", Decimal("0.78"), "电机转速长期不一致", "至少 30% 的有效采样点出现电机转速差异超阈值。", {"peak_imbalance_ratio": str(peak), "sample_count": len(ratios)}, "检查桨叶、电机轴承、电机线圈和 ESC 输出，并进行空载对比测试。")]
        return []


def _flagged_rows(rows: list[dict[str, Any]], key_terms: tuple[str, ...]) -> list[dict[str, Any]]:
    flagged = []
    for row in rows:
        for key, value in row.items():
            if any(term in key.lower() for term in key_terms) and str(value).strip().lower() not in {"", "0", "0.0", "false", "none", "normal", "ok"}:
                flagged.append({"field": key, "value": str(value)[:120]})
                break
    return flagged


class EscAnalyzer:
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        flagged = _flagged_rows(parsed.get("time_series", []), ("esc_error", "esc_status", "esc_warning"))
        if flagged:
            return [Finding("esc", "high", Decimal("0.82"), "ESC 状态异常", f"检测到 {len(flagged)} 条 ESC 异常标记。", {"samples": flagged[:20]}, "读取 ESC 错误码，检查电机相线、焊点和电调温度。")]
        return []


class ImuAnalyzer:
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        errors = _numbers(parsed.get("time_series", []), ("attitude_error", "imu_attitude_error", "attitude_error_deg"))
        if errors and max(map(abs, errors)) > THRESHOLDS["imu_attitude_error_deg"]:
            peak = max(map(abs, errors))
            return [Finding("imu", "high", Decimal("0.70"), "姿态补偿异常", f"姿态误差峰值达到 {peak}°。", {"peak_attitude_error_deg": str(peak)}, "检查 IMU 固定、减震结构并使用官方工具重新校准。")]
        return []


class CompassAnalyzer:
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        flagged = _flagged_rows(parsed.get("time_series", []), ("compass_error", "mag_error", "compass_warning"))
        levels = _numbers(parsed.get("time_series", []), ("compass_interference", "mag_interference"))
        if flagged or (levels and max(levels) > THRESHOLDS["compass_interference_level"]):
            return [Finding("compass", "medium", Decimal("0.76"), "指南针异常或磁干扰", "日志包含指南针错误标记或磁干扰水平超过阈值。", {"flags": flagged[:20], "max_interference": str(max(levels)) if levels else None}, "远离磁性物体复测，检查指南针排线和安装方向后重新校准。")]
        return []


class FlightEventAnalyzer:
    KEYWORDS = ("signal lost", "disconnect", "lost link", "go home", "return to home", "rth", "失联", "返航")

    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        matched = []
        for row in parsed.get("time_series", []):
            text = " ".join(str(row.get(key, "")) for key in ("event", "message", "warning", "flight_event")).lower()
            if text and any(keyword in text for keyword in self.KEYWORDS):
                matched.append(text[:240])
        for event in parsed.get("events", []):
            text = str(event).lower()
            if any(keyword in text for keyword in self.KEYWORDS):
                matched.append(text[:240])
        if matched:
            return [Finding("flight_event", "medium", Decimal("0.88"), "飞行中触发失联或自动返航", f"匹配到 {len(matched)} 条相关飞行事件。", {"events": matched[:20]}, "核对遥控链路、天线、环境干扰和返航点设置。")]
        return []


class GpsAnalyzer:
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        sats = _numbers(parsed.get("time_series", []), ("gps_satellites", "satellites", "gpsnum"))
        if sats and sum(v < THRESHOLDS["gps_satellites_low"] for v in sats) / len(sats) > 0.5:
            return [Finding("gps", "medium", Decimal("0.75"), "GPS 星数持续偏低", "超过一半采样点的 GPS 星数低于阈值。", {"threshold": str(THRESHOLDS["gps_satellites_low"]), "samples": len(sats)}, "检查 GPS 模块、天线连接和飞行环境。")]
        return []


class NoopAnalyzer:
    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        return []


class AnalyzerEngine:
    def __init__(self):
        self.analyzers: list[Analyzer] = [BatteryAnalyzer(), MotorAnalyzer(), EscAnalyzer(), ImuAnalyzer(), GpsAnalyzer(), CompassAnalyzer(), FlightEventAnalyzer()]

    def analyze(self, parsed: dict[str, Any]) -> list[Finding]:
        findings: list[Finding] = []
        for analyzer in self.analyzers:
            findings.extend(analyzer.analyze(parsed))
        return findings
