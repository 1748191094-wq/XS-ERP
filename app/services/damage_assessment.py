from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import BusinessError
from app.models.entities import (
    Attachment,
    DamageAssessment,
    DamageAssessmentResult,
    DamageSopStep,
    DamageSopTemplate,
    Diagnosis,
    DroneDevice,
    PointMap,
    PointMarker,
    RepairOrder,
)
from app.schemas.domain import (
    DamageAssessmentComplete,
    DamageAssessmentResultUpdate,
)
from app.services.numbering import make_no
from app.services.orders import RepairOrderService
from app.services.timeline import _order_timeline


def template_applies(template: DamageSopTemplate, device: DroneDevice) -> bool:
    brand = template.brand.strip().casefold()
    model_pattern = template.model_pattern.strip().casefold()
    brand_matches = brand in {"*", "通用", "generic"} or brand == device.brand.strip().casefold()
    model_matches = model_pattern in {"*", "通用", "generic"} or model_pattern in device.model.strip().casefold()
    return brand_matches and model_matches


def step_snapshot(step: DamageSopStep) -> dict:
    return {
        "step_code": step.step_code,
        "sort_order": step.sort_order,
        "section": step.section,
        "title": step.title,
        "instruction": step.instruction,
        "check_type": step.check_type,
        "required": step.required,
        "module_name": step.module_name,
        "expected_result": step.expected_result,
        "fail_conclusion": step.fail_conclusion,
        "risk_level": step.risk_level,
        "point_map_id": step.point_map_id,
        "point_marker_id": step.point_marker_id,
    }


def load_assessment(db: Session, assessment_id: int) -> DamageAssessment:
    assessment = db.scalar(
        select(DamageAssessment)
        .where(DamageAssessment.id == assessment_id, DamageAssessment.deleted_at.is_(None))
        .options(selectinload(DamageAssessment.results))
    )
    if not assessment:
        raise BusinessError("定损任务不存在", code="damage_assessment_not_found", status_code=404)
    return assessment


def create_assessment(
    db: Session,
    *,
    order: RepairOrder,
    device: DroneDevice,
    template: DamageSopTemplate,
    operator_id: int | None,
) -> DamageAssessment:
    steps = list(db.scalars(
        select(DamageSopStep)
        .where(DamageSopStep.template_id == template.id)
        .order_by(DamageSopStep.sort_order, DamageSopStep.id)
    ))
    if not steps:
        raise BusinessError("SOP 模板尚未配置检查步骤", code="damage_sop_has_no_steps", status_code=409)
    if template.status != "published":
        raise BusinessError("只能使用已发布的 SOP 模板创建定损", code="damage_sop_not_published", status_code=409)
    if not template_applies(template, device):
        raise BusinessError("所选 SOP 不适用于当前设备品牌或型号", code="damage_sop_device_mismatch", status_code=409)

    assessment = DamageAssessment(
        assessment_no=make_no("DA"),
        repair_order_id=order.id,
        device_id=device.id,
        template_id=template.id,
        template_version=template.version,
        status="in_progress",
        operator_id=operator_id,
    )
    db.add(assessment)
    db.flush()
    for step in steps:
        db.add(DamageAssessmentResult(
            assessment_id=assessment.id,
            sop_step_id=step.id,
            step_code=step.step_code,
            sort_order=step.sort_order,
            step_snapshot_json=step_snapshot(step),
            result="pending",
            point_marker_id=step.point_marker_id,
        ))
    db.flush()
    return load_assessment(db, assessment.id)


def update_result(
    db: Session,
    *,
    assessment: DamageAssessment,
    result_id: int,
    payload: DamageAssessmentResultUpdate,
    user_id: int | None,
) -> DamageAssessmentResult:
    if assessment.status != "in_progress":
        raise BusinessError("已结束的定损任务不能修改", code="damage_assessment_closed", status_code=409)
    result = db.get(DamageAssessmentResult, result_id)
    if not result or result.assessment_id != assessment.id:
        raise BusinessError("定损步骤不存在", code="damage_assessment_result_not_found", status_code=404)
    if payload.evidence_attachment_id:
        attachment = db.get(Attachment, payload.evidence_attachment_id)
        if not attachment or attachment.repair_order_id != assessment.repair_order_id:
            raise BusinessError("证据附件不属于当前维修工单", code="assessment_evidence_mismatch", status_code=409)
    for name, value in payload.model_dump().items():
        setattr(result, name, value)
    result.source = "manual"
    result.source_ref_json = None
    result.completed_by = user_id
    result.completed_at = datetime.now(timezone.utc)
    db.flush()
    return result


# Matches the shipped generic SOP; custom templates require an explicit selection.
DIAGNOSIS_STEP_MAP = {
    "battery": "PWR-040", "motor": "FUNC-070", "esc": "FUNC-070",
    "imu": "FUNC-070", "compass": "FUNC-070", "gps": "LINK-060",
    "flight_event": "FAULT-080",
}


def adopt_diagnosis(db: Session, *, assessment: DamageAssessment, diagnosis_id: int,
                   result_id: int, payload: DamageAssessmentResultUpdate, user_id: int) -> DamageAssessmentResult:
    diagnosis = db.get(Diagnosis, diagnosis_id)
    if not diagnosis or diagnosis.repair_order_id != assessment.repair_order_id:
        raise BusinessError("系统建议不属于当前工单", code="diagnosis_order_mismatch", status_code=409)
    if diagnosis.damage_assessment_id is not None:
        raise BusinessError("该建议已被采纳，请在对应步骤中复核", code="diagnosis_already_adopted", status_code=409)
    result = db.get(DamageAssessmentResult, result_id)
    if not result or result.assessment_id != assessment.id:
        raise BusinessError("定损步骤不存在", code="damage_assessment_result_not_found", status_code=404)
    if result.result != "pending":
        raise BusinessError("该步骤已有结果，请直接复核，避免覆盖", code="assessment_result_exists", status_code=409)
    result = update_result(db, assessment=assessment, result_id=result_id, payload=payload, user_id=user_id)
    result.source = "log_rule"
    result.source_ref_json = {"diagnosis_id": diagnosis.id}
    diagnosis.damage_assessment_id = assessment.id
    diagnosis.sop_step_code = result.step_code
    diagnosis.confirmed_result = payload.result
    diagnosis.confirmed_by = user_id
    _order_timeline(db, assessment.repair_order_id, "diagnosis_adopted", f"人工确认系统建议：{diagnosis.title}", user_id,
                    {"diagnosis_id": diagnosis.id, "damage_assessment_id": assessment.id, "step_code": result.step_code})
    db.flush()
    return result


def complete_assessment(
    db: Session,
    *,
    assessment: DamageAssessment,
    payload: DamageAssessmentComplete,
    user_id: int | None = None,
) -> DamageAssessment:
    if assessment.status != "in_progress":
        raise BusinessError("定损任务已经结束", code="damage_assessment_closed", status_code=409)
    pending_required = [
        item.step_code
        for item in assessment.results
        if item.step_snapshot_json.get("required", True) and item.result == "pending"
    ]
    if pending_required:
        raise BusinessError(
            f"仍有 {len(pending_required)} 个必做步骤未完成",
            code="damage_assessment_incomplete",
            status_code=409,
        )
    assessment.status = "completed"
    assessment.conclusion = payload.conclusion
    assessment.responsibility = payload.responsibility
    assessment.repair_recommendation = payload.repair_recommendation
    assessment.estimated_cost = payload.estimated_cost
    assessment.completed_at = datetime.now(timezone.utc)
    order = db.get(RepairOrder, assessment.repair_order_id)
    if not order or order.deleted_at is not None:
        raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
    if order.status in {"pending_inspection", "inspecting"}:
        RepairOrderService.change_status(db, order, "pending_quote", changed_by=user_id, reason=f"完成定损 {assessment.assessment_no}")
    _order_timeline(db, order.id, "assessment_completed", f"完成定损 {assessment.assessment_no}", user_id, {"damage_assessment_id": assessment.id})
    db.flush()
    return assessment


def assessment_detail(db: Session, assessment: DamageAssessment) -> dict:
    template = db.get(DamageSopTemplate, assessment.template_id)
    order = db.get(RepairOrder, assessment.repair_order_id)
    device = db.get(DroneDevice, assessment.device_id)
    map_ids = {
        int(item.step_snapshot_json["point_map_id"])
        for item in assessment.results
        if item.step_snapshot_json.get("point_map_id")
    }
    point_maps = []
    if map_ids:
        maps = list(db.scalars(
            select(PointMap)
            .where(PointMap.id.in_(map_ids))
            .options(selectinload(PointMap.markers))
        ))
        for point_map in maps:
            point_maps.append({
                "map": point_map,
                "markers": point_map.markers,
                "image_url": (
                    f"/api/files/attachment/{point_map.image_attachment_id}"
                    if point_map.image_attachment_id else None
                ),
            })
    completed = sum(1 for item in assessment.results if item.result != "pending")
    failed = sum(1 for item in assessment.results if item.result == "fail")
    return {
        "assessment": assessment,
        "template": template,
        "order": order,
        "device": device,
        "results": assessment.results,
        "suggestions": [
            {"diagnosis": item, "suggested_step_code": DIAGNOSIS_STEP_MAP.get(item.diagnosis_type)}
            for item in db.scalars(select(Diagnosis).where(Diagnosis.repair_order_id == assessment.repair_order_id).order_by(Diagnosis.id.desc()))
        ],
        "point_maps": point_maps,
        "progress": {
            "completed": completed,
            "total": len(assessment.results),
            "failed": failed,
            "percent": round(completed * 100 / len(assessment.results)) if assessment.results else 0,
        },
    }


def latest_completed_assessment(db: Session, order_id: int) -> DamageAssessment | None:
    return db.scalar(select(DamageAssessment).where(
        DamageAssessment.repair_order_id == order_id,
        DamageAssessment.status == "completed",
        DamageAssessment.deleted_at.is_(None),
    ).order_by(DamageAssessment.completed_at.desc(), DamageAssessment.id.desc()).limit(1))
