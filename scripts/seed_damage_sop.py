from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.core.database import SessionLocal, create_schema
from app.models.entities import DamageSopStep, DamageSopTemplate


TEMPLATE_TITLE = "数码设备基础定损 SOP"
TEMPLATE_VERSION = "1.0"
SOURCE_REFERENCE = "门店自建通用流程"


STEPS = [
    {
        "step_code": "INTAKE-010", "sort_order": 10, "section": "收机建档",
        "title": "核对设备与随附件物品",
        "instruction": "核对品牌、型号、序列号、客户故障描述和随附件物品；拍摄收机外观与序列号照片。",
        "check_type": "photo", "expected_result": "设备信息与工单一致，随附件物品已逐项记录。",
        "fail_conclusion": "设备或随附件信息不一致，暂停拆机并与客户确认。",
    },
    {
        "step_code": "VIS-020", "sort_order": 20, "section": "外观与责任痕迹",
        "title": "检查碰撞、变形、缺件和维修痕迹",
        "instruction": "在充足照明下检查外壳、接口、紧固件、封签和关键结构，记录异常位置并留图。",
        "check_type": "visual", "expected_result": "无明显外力、缺件或异常拆修痕迹。",
        "fail_conclusion": "存在外力或既往维修迹象，需单独列入定损结论。",
    },
    {
        "step_code": "LIQ-030", "sort_order": 30, "section": "外观与责任痕迹",
        "title": "检查进液、腐蚀和高温痕迹",
        "instruction": "检查防水标签、接口、屏蔽罩边缘和可见板件；发现液体或腐蚀时不得直接通电。",
        "check_type": "visual", "expected_result": "未发现进液、腐蚀、烧蚀或异常气味。",
        "fail_conclusion": "存在进液、腐蚀或高温损伤，需先断电清洁并扩大检查范围。",
        "risk_level": "danger",
    },
    {
        "step_code": "PWR-040", "sort_order": 40, "section": "断电电气检查",
        "title": "断电检查主供电路径",
        "instruction": "断开电池和外部电源，使用合适量程检查输入端对地阻值、保险和明显短路；具体点位以对应机型点位图为准。",
        "check_type": "measurement", "expected_result": "无明显短路，保险与供电路径连续性符合对应机型参考值。",
        "fail_conclusion": "供电路径异常，禁止直接通电，转入板级供电排查。",
        "risk_level": "caution",
    },
    {
        "step_code": "BOOT-050", "sort_order": 50, "section": "受控通电",
        "title": "记录启动现象和电流行为",
        "instruction": "确认无短路风险后，使用适配电源或合格电池受控通电，记录待机、启动和稳定阶段现象；不得超过设备额定参数。",
        "check_type": "measurement", "expected_result": "设备可正常启动，电流和温升无明显异常。",
        "fail_conclusion": "启动失败、异常大电流或局部快速升温，立即断电并记录。",
        "risk_level": "danger",
    },
    {
        "step_code": "LINK-060", "sort_order": 60, "section": "连接与通信",
        "title": "检查接口、充电和基础通信",
        "instruction": "检查充电、USB、网络或无线连接，以及电脑或官方应用识别情况；只执行已授权且与机型匹配的只读检查。",
        "check_type": "functional", "expected_result": "接口和基础通信正常，无反复掉线或识别异常。",
        "fail_conclusion": "接口、连接线、保护器件或主控通信路径可能异常。",
    },
    {
        "step_code": "FUNC-070", "sort_order": 70, "section": "整机功能",
        "title": "执行与产品类别匹配的功能检查",
        "instruction": "按产品类别检查显示、按键、传感器、相机、音频、动力、定位或其他核心功能；不具备安全条件的项目标记为不适用。",
        "check_type": "functional", "expected_result": "核心功能和客户描述以外功能均可正常工作。",
        "fail_conclusion": "记录异常模块、复现条件和影响范围。",
    },
    {
        "step_code": "FAULT-080", "sort_order": 80, "section": "故障复现",
        "title": "复现客户描述故障",
        "instruction": "按客户描述的环境和操作顺序进行可控复现，记录出现频率、触发条件、错误提示和可恢复方式。",
        "check_type": "decision", "expected_result": "故障是否复现、触发条件和影响范围已有明确记录。",
        "fail_conclusion": "无法稳定复现时不得猜测损坏部件，应保留为待进一步检测。",
    },
    {
        "step_code": "SCOPE-090", "sort_order": 90, "section": "范围判定",
        "title": "确定损坏范围和建议维修层级",
        "instruction": "综合外观、测量、功能和故障复现结果，区分整机、模块、板件或器件级损坏，并标注仍需确认的项目。",
        "check_type": "decision", "expected_result": "损坏范围、证据、风险和下一步检测建议可以相互对应。",
        "fail_conclusion": "证据不足时保持待确认，不直接生成确定性结论。",
    },
    {
        "step_code": "REPORT-100", "sort_order": 100, "section": "结论与报价准备",
        "title": "完成定损结论和维修建议",
        "instruction": "填写定损结论、责任痕迹、维修建议和费用预估；异常点必须能够追溯到检查记录或附件证据。",
        "check_type": "decision", "expected_result": "结论完整、证据可追溯，可进入人工报价或高级专员复核。",
        "fail_conclusion": "资料不完整时不得向客户发送最终定损结论。",
    },
]


def main() -> None:
    create_schema()
    with SessionLocal() as db:
        existing = db.scalar(select(DamageSopTemplate).where(
            DamageSopTemplate.brand == "通用",
            DamageSopTemplate.model_pattern == "*",
            DamageSopTemplate.title == TEMPLATE_TITLE,
            DamageSopTemplate.version == TEMPLATE_VERSION,
        ))
        if existing:
            print(f"exists:{existing.id}")
            return

        template = DamageSopTemplate(
            brand="通用",
            product_category="数码产品",
            model_pattern="*",
            title=TEMPLATE_TITLE,
            version=TEMPLATE_VERSION,
            status="published",
            description="适用于无人机、相机、遥控器、电脑及其他数码设备的基础定损骨架；具体板级数值需绑定对应机型点位图。",
            source_reference=SOURCE_REFERENCE,
            access_level="internal",
            published_at=datetime.now(timezone.utc),
        )
        db.add(template)
        db.flush()
        for item in STEPS:
            step_data = {"required": True, "risk_level": "normal", **item}
            db.add(DamageSopStep(template_id=template.id, **step_data))
        db.commit()
        print(f"created:{template.id};steps:{len(STEPS)}")


if __name__ == "__main__":
    main()
