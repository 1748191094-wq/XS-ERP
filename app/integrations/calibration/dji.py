from __future__ import annotations

from typing import Any


DJI_GIMBAL_SOURCES = {
    "mobile_sdk_v4": "https://developer.dji.com/api-reference/android-api/Components/Gimbal/DJIGimbal.html",
    "mobile_sdk_v5": "https://developer.dji.com/api-reference-v5/android-api/Components/IKeyManager/Key_Gimbal_GimbalKey.html",
    "payload_sdk": "https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/basic-function/gimbal-function.html",
    "official_repair_guide": "https://repair.dji.com/help/content?customId=01700006819&lang=en&paperDocType=ARTICLE&re=US&spaceId=17",
}


def gimbal_calibration_capability(*, brand: str, model: str) -> dict[str, Any]:
    is_dji = brand.strip().lower() in {"dji", "大疆", "大疆创新"}
    return {
        "brand": brand,
        "model": model,
        "is_dji": is_dji,
        "recommended_method": "official_app_auto_calibration" if is_dji else "manufacturer_official_workflow",
        "recommended_tool": (
            "DJI Fly / DJI GO 4 / DJI Pilot 2（以该机型官方入口为准）"
            if is_dji else "设备厂商官方应用或维修工具"
        ),
        "desktop_direct_calibration_supported": False,
        "mobile_sdk_bridge": {
            "possible": is_dji,
            "status": "not_connected",
            "requirements": ["受 DJI Mobile SDK 支持的产品", "Android 桥接应用", "真机连接与逐型号回归测试"],
        },
        "payload_sdk_scope": "仅适用于自行开发的 PSDK 云台负载，不等于消费级原装云台维修接口",
        "preconditions": [
            "设备必须静止且水平放置",
            "不得在飞行或手持移动中执行",
            "可调载荷必须安装完整并提前配平",
            "确认电量充足，标定过程中不要断电或移动设备",
        ],
        "workflow": [
            "在对应 DJI 官方应用中连接设备并确认机型与固件",
            "进入云台设置，选择自动标定并等待官方应用返回结果",
            "记录应用名称、版本、固件版本、开始/结束时间和结果",
            "执行水平、回中、俯仰/横滚行程与异常噪声复测",
        ],
        "research_only": {
            "private_protocol_tools_enabled": False,
            "reason": "逆向数据包和 DAT 工具具有机型/固件差异，未通过真机验证前不得用于生产标定",
        },
        "sources": DJI_GIMBAL_SOURCES,
    }


class DJIOfficialWorkflowProvider:
    """Records an official-app calibration; it never claims direct device control."""

    def record(
        self,
        *,
        brand: str,
        model: str,
        tool_name: str,
        before: dict[str, Any],
        after: dict[str, Any],
        operator_id: int | None,
    ) -> dict[str, Any]:
        return {
            "mode": "official_tool_record",
            "tool_name": tool_name,
            "device": {"brand": brand, "model": model},
            "before": before,
            "after": after,
            "operator_id": operator_id,
            "automatic_dji_protocol_used": False,
            "capability_snapshot": gimbal_calibration_capability(brand=brand, model=model),
        }
