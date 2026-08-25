from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    profile_id: str
    display_name: str
    family: str
    calibration_kinds: tuple[str, ...]
    evidence_level: str
    evidence_note: str
    live_enabled: bool = False

    def to_dict(self) -> dict:
        value = asdict(self)
        value["calibration_kinds"] = list(self.calibration_kinds)
        return value


# 配置仅代表研究证据；完成硬件验证前禁用实机执行。
PROFILES = {
    profile.profile_id: profile
    for profile in (
        CalibrationProfile(
            profile_id="wm100",
            display_name="DJI Spark / WM100（研究档案）",
            family="WM100",
            calibration_kinds=("joint_coarse", "linear_hall"),
            evidence_level="community_reported",
            evidence_note="开源维修工具报告过关节粗标定；线性霍尔流程仍需 维护方真机验证。",
        ),
        CalibrationProfile(
            profile_id="wm230",
            display_name="DJI Mavic Air / WM230（研究档案）",
            family="WM230",
            calibration_kinds=("joint_coarse", "linear_hall"),
            evidence_level="community_reported",
            evidence_note="存在社区维修测试记录；固件差异和应答方式尚未由维护方复核。",
        ),
        CalibrationProfile(
            profile_id="wm240",
            display_name="DJI Mavic 2 / WM240（研究档案）",
            family="WM240",
            calibration_kinds=("joint_coarse", "linear_hall"),
            evidence_level="community_reported",
            evidence_note="存在社区报告；当前仅允许协议模拟，不允许连接真机执行。",
        ),
    )
}


def get_profile(profile_id: str) -> CalibrationProfile:
    try:
        return PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown calibration profile: {profile_id}") from exc


def list_profiles() -> list[dict]:
    return [profile.to_dict() for profile in PROFILES.values()]
