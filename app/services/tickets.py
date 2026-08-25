from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models.entities import (
    Customer,
    DroneDevice,
    ProcessingGroup,
    ProcessingGroupMember,
    RepairOrder,
    ServiceTicket,
    ServiceTicketCollaborator,
    ServiceTicketNote,
    ServiceTicketTimeline,
    SpecialistEscalation,
    User,
    utcnow,
)
from app.services.numbering import make_no


REPAIR_TO_TICKET_STATUS = {
    "pending_inspection": "open",
    "inspecting": "in_progress",
    "pending_quote": "waiting_internal",
    "quoted": "waiting_customer",
    "customer_confirmed": "in_progress",
    "repairing": "in_progress",
    "pending_test": "in_progress",
    "pending_shipping": "waiting_internal",
    "completed": "resolved",
    "cancelled": "cancelled",
}

TICKET_TYPE_LABELS = {
    "repair": "维修工单",
    "consultation": "客户咨询",
    "quote_followup": "报价跟进",
    "after_sales_followup": "售后回访",
    "complaint": "投诉处理",
    "logistics_exception": "物流异常",
    "technical_support": "技术支持",
    "specialist_assistance": "高级专员协助",
    "retail": "零售",
    "replacement": "置换业务",
    "recycle": "回收业务",
}

VALID_TICKET_STATUSES = {
    "open", "assigned", "in_progress", "waiting_customer", "waiting_internal",
    "resolved", "closed", "cancelled",
}
TERMINAL_TICKET_STATUSES = {"closed", "cancelled"}
TICKET_STATUS_TRANSITIONS = {
    "open": {"assigned", "in_progress", "waiting_customer", "waiting_internal", "cancelled"},
    "assigned": {"open", "in_progress", "waiting_customer", "waiting_internal", "cancelled"},
    "in_progress": {"waiting_customer", "waiting_internal", "resolved", "cancelled"},
    "waiting_customer": {"in_progress", "waiting_internal", "resolved", "cancelled"},
    "waiting_internal": {"in_progress", "waiting_customer", "resolved", "cancelled"},
    "resolved": {"closed"},
    "closed": set(),
    "cancelled": set(),
}
ESCALATION_TRANSITIONS = {
    "submitted": {"accepted", "returned", "cancelled"},
    "returned": {"submitted", "accepted", "cancelled"},
    "accepted": {"in_progress", "completed", "returned", "cancelled"},
    "in_progress": {"completed", "returned", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


class TicketService:
    @staticmethod
    def _active_user(db: Session, user_id: int | None, *, code: str, label: str) -> User | None:
        if user_id is None:
            return None
        user = db.get(User, user_id)
        if not user or not user.enabled:
            raise BusinessError(f"{label}不存在或已停用", code=code, status_code=404)
        return user

    @staticmethod
    def _active_group(db: Session, group_id: int | None, *, code: str, label: str) -> ProcessingGroup | None:
        if group_id is None:
            return None
        group = db.get(ProcessingGroup, group_id)
        if not group or not group.enabled:
            raise BusinessError(f"{label}不存在或已停用", code=code, status_code=404)
        return group

    @staticmethod
    def _apply_status(
        ticket: ServiceTicket,
        target: str,
        *,
        allow_terminal_exit: bool = False,
        resolved_at=None,
    ) -> tuple[str, str]:
        if target not in VALID_TICKET_STATUSES:
            raise BusinessError("未知的服务工单状态", code="invalid_ticket_status", status_code=400)
        previous = ticket.status
        if previous == target:
            return previous, target
        if previous in TERMINAL_TICKET_STATUSES and not allow_terminal_exit:
            raise BusinessError("已关闭或已取消的服务工单不能直接重新流转", code="ticket_terminal_state", status_code=409)
        if not allow_terminal_exit and target not in TICKET_STATUS_TRANSITIONS.get(previous, set()):
            raise BusinessError(
                f"服务工单状态不能从 {previous} 直接变更为 {target}",
                code="invalid_ticket_transition",
                status_code=409,
            )

        now = utcnow()
        ticket.status = target
        if target in {"assigned", "in_progress"} and not ticket.first_response_at:
            ticket.first_response_at = now
        if previous == "resolved" and target != "resolved":
            ticket.resolved_at = None
        if previous == "closed" and target != "closed":
            ticket.closed_at = None
        if target == "resolved":
            ticket.resolved_at = resolved_at or now
            ticket.closed_at = None
        elif target == "closed":
            ticket.closed_at = now
        return previous, target

    @staticmethod
    def _timeline(
        db: Session,
        ticket: ServiceTicket,
        event_type: str,
        summary: str,
        *,
        actor_id: int | None,
        from_status: str | None = None,
        to_status: str | None = None,
        details: dict | None = None,
    ) -> None:
        db.add(ServiceTicketTimeline(
            ticket_id=ticket.id,
            event_type=event_type,
            summary=summary[:300],
            actor_id=actor_id,
            from_status=from_status,
            to_status=to_status,
            details_json=details,
        ))

    @classmethod
    def ensure_for_repair_order(
        cls, db: Session, order: RepairOrder, *, created_by: int | None = None
    ) -> ServiceTicket:
        existing = db.scalar(select(ServiceTicket).where(ServiceTicket.repair_order_id == order.id))
        if existing:
            if existing.deleted_at is not None:
                raise BusinessError(
                    "维修工单关联的服务工单位于回收站，请先恢复后再继续",
                    code="linked_ticket_deleted",
                    status_code=409,
                )
            return existing
        ticket = ServiceTicket(
            ticket_no=f"TKT-{order.order_no}",
            ticket_type="repair",
            title=f"维修服务：{order.fault_description[:180]}",
            description=order.fault_description,
            status=REPAIR_TO_TICKET_STATUS.get(order.status, "open"),
            priority=order.priority,
            customer_id=order.customer_id,
            device_id=order.device_id,
            repair_order_id=order.id,
            current_owner_id=order.engineer_id,
            processing_group_id=order.processing_group_id,
            created_by=created_by,
            due_at=order.expected_finish_at,
            resolved_at=order.completed_at if order.status == "completed" else None,
        )
        db.add(ticket)
        db.flush()
        cls._timeline(
            db, ticket, "created", "由维修工单自动建立统一服务工单",
            actor_id=created_by,
            details={"description": ticket.description, "version": 1, "is_original": True},
        )
        return ticket

    @classmethod
    def sync_repair_order_status(
        cls, db: Session, order: RepairOrder, *, actor_id: int | None, reason: str | None
    ) -> None:
        ticket = db.scalar(select(ServiceTicket).where(ServiceTicket.repair_order_id == order.id))
        if ticket and ticket.deleted_at is not None:
            raise BusinessError(
                "维修工单关联的服务工单位于回收站，请先恢复后再继续",
                code="linked_ticket_deleted",
                status_code=409,
            )
        if not ticket:
            ticket = cls.ensure_for_repair_order(db, order, created_by=actor_id)
        target = REPAIR_TO_TICKET_STATUS.get(order.status, ticket.status)
        ticket.priority = order.priority
        ticket.current_owner_id = order.engineer_id
        ticket.processing_group_id = order.processing_group_id
        ticket.due_at = order.expected_finish_at
        previous = ticket.status
        if target != previous:
            cls._apply_status(
                ticket,
                target,
                allow_terminal_exit=True,
                resolved_at=order.completed_at,
            )
            cls._timeline(
                db, ticket, "repair_status_synced", reason or "同步维修工单状态",
                actor_id=actor_id, from_status=previous, to_status=target,
            )

    @classmethod
    def create(cls, db: Session, payload, *, created_by: int) -> ServiceTicket:
        customer = db.get(Customer, payload.customer_id) if payload.customer_id else None
        if payload.customer_id and (not customer or customer.deleted_at is not None):
            raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
        device = db.get(DroneDevice, payload.device_id) if payload.device_id else None
        if payload.device_id and not device:
            raise BusinessError("设备不存在", code="device_not_found", status_code=404)
        if device and payload.customer_id and device.customer_id != payload.customer_id:
            raise BusinessError("设备不属于所选客户", code="device_customer_mismatch", status_code=409)
        order = db.get(RepairOrder, payload.repair_order_id) if payload.repair_order_id else None
        if payload.repair_order_id and (not order or order.deleted_at is not None):
            raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
        if order:
            if payload.ticket_type != "repair":
                raise BusinessError("关联维修工单时工单类型必须为 repair", code="ticket_type_mismatch")
            if payload.customer_id is not None and payload.customer_id != order.customer_id:
                raise BusinessError("服务工单客户与维修工单不一致", code="ticket_order_customer_mismatch", status_code=409)
            if payload.device_id is not None and payload.device_id != order.device_id:
                raise BusinessError("服务工单设备与维修工单不一致", code="ticket_order_device_mismatch", status_code=409)
            existing = db.scalar(select(ServiceTicket).where(ServiceTicket.repair_order_id == order.id))
            if existing:
                if existing.deleted_at is not None:
                    raise BusinessError(
                        "维修工单关联的服务工单位于回收站，请先恢复",
                        code="linked_ticket_deleted",
                        status_code=409,
                    )
                raise BusinessError("该维修工单已存在统一服务工单", code="ticket_already_exists", status_code=409)
        owner_id = order.engineer_id if order else payload.current_owner_id
        cls._active_user(db, owner_id, code="owner_not_found", label="负责人")
        processing_group_id = order.processing_group_id if order else payload.processing_group_id
        if not processing_group_id and payload.current_owner_id:
            candidates = list(db.scalars(
                select(ProcessingGroupMember.group_id)
                .join(ProcessingGroup, ProcessingGroup.id == ProcessingGroupMember.group_id)
                .where(
                    ProcessingGroupMember.user_id == payload.current_owner_id,
                    ProcessingGroup.group_type == "service",
                    ProcessingGroup.enabled.is_(True),
                )
                .order_by(ProcessingGroup.id)
                .limit(2)
            ))
            if len(candidates) == 1:
                processing_group_id = candidates[0]
        if processing_group_id:
            cls._active_group(db, processing_group_id, code="group_not_found", label="处理组")

        ticket = ServiceTicket(
            ticket_no=make_no("TKT"),
            ticket_type=payload.ticket_type,
            title=payload.title,
            description=payload.description,
            status=REPAIR_TO_TICKET_STATUS.get(order.status, "open") if order else (
                "assigned" if owner_id or processing_group_id else "open"
            ),
            priority=payload.priority,
            customer_id=order.customer_id if order else payload.customer_id,
            device_id=order.device_id if order else payload.device_id,
            repair_order_id=payload.repair_order_id,
            current_owner_id=owner_id,
            processing_group_id=processing_group_id,
            created_by=created_by,
            due_at=payload.due_at,
            replacement_inspection_result=payload.replacement_inspection_result,
            trade_in_credit=payload.trade_in_credit,
            return_reference=payload.return_reference,
            outbound_to_customer_tracking_no=payload.outbound_to_customer_tracking_no,
        )
        db.add(ticket)
        db.flush()
        cls._timeline(
            db, ticket, "created", "创建统一服务工单",
            actor_id=created_by,
            details={"description": ticket.description, "version": 1, "is_original": True},
        )
        for user_id in dict.fromkeys(payload.collaborator_ids):
            cls._active_user(db, user_id, code="collaborator_not_found", label=f"协助成员 #{user_id}")
            db.add(ServiceTicketCollaborator(
                ticket_id=ticket.id, user_id=user_id, added_by=created_by
            ))
        db.flush()
        return ticket

    @classmethod
    def assign(cls, db: Session, ticket: ServiceTicket, payload, *, actor_id: int) -> ServiceTicket:
        cls._active_user(db, payload.current_owner_id, code="owner_not_found", label="负责人")
        cls._active_group(db, payload.processing_group_id, code="group_not_found", label="处理组")
        previous_owner, previous_group = ticket.current_owner_id, ticket.processing_group_id
        previous_status = ticket.status
        ticket.current_owner_id = payload.current_owner_id
        ticket.processing_group_id = payload.processing_group_id
        if ticket.repair_order_id:
            order = db.get(RepairOrder, ticket.repair_order_id)
            if order:
                order.engineer_id = payload.current_owner_id
                order.processing_group_id = payload.processing_group_id
        if ticket.status in {"open", "assigned"}:
            ticket.status = "assigned" if ticket.current_owner_id or ticket.processing_group_id else "open"
        cls._timeline(
            db, ticket, "assigned", f"工单转派：{payload.reason}", actor_id=actor_id,
            from_status=previous_status, to_status=ticket.status,
            details={"from_owner_id": previous_owner, "to_owner_id": ticket.current_owner_id,
                     "from_group_id": previous_group, "to_group_id": ticket.processing_group_id},
        )
        db.flush()
        return ticket

    @classmethod
    def change_status(cls, db: Session, ticket: ServiceTicket, payload, *, actor_id: int) -> ServiceTicket:
        previous, target = cls._apply_status(ticket, payload.status)
        if previous == target:
            return ticket
        cls._timeline(
            db, ticket, "status_changed", payload.reason, actor_id=actor_id,
            from_status=previous, to_status=target,
        )
        db.flush()
        return ticket

    @classmethod
    def change_type(cls, db: Session, ticket: ServiceTicket, payload, *, actor_id: int) -> ServiceTicket:
        previous = ticket.ticket_type
        target = payload.ticket_type
        if payload.expected_ticket_type and payload.expected_ticket_type != previous:
            raise BusinessError(
                "工单类型已被其他成员更新，请刷新后重新确认",
                code="ticket_type_change_conflict",
                status_code=409,
            )
        if previous == target:
            return ticket
        if ticket.repair_order_id:
            raise BusinessError(
                "关联维修工单的类型由系统维护，不能手工更改",
                code="linked_repair_ticket_type_locked",
                status_code=409,
            )
        if target == "repair":
            raise BusinessError(
                "维修类型必须由维修工单自动建立，不能手工转换",
                code="repair_ticket_requires_order",
                status_code=409,
            )
        if any(quote.deleted_at is None for quote in ticket.quotes):
            raise BusinessError(
                "该服务工单已有有效报价，需先处理报价后才能更改类型",
                code="ticket_type_has_active_quotes",
                status_code=409,
            )
        if previous == "replacement" and target != "replacement" and any((
            ticket.replacement_inspection_result,
            ticket.trade_in_credit is not None,
            ticket.return_reference,
            ticket.outbound_to_customer_tracking_no,
        )):
            raise BusinessError(
                "该置换工单已有业务记录，清空置换信息后才能更改类型",
                code="ticket_type_has_replacement_data",
                status_code=409,
            )
        ticket.ticket_type = target
        previous_label = TICKET_TYPE_LABELS.get(previous, previous)
        target_label = TICKET_TYPE_LABELS.get(target, target)
        cls._timeline(
            db,
            ticket,
            "type_changed",
            f"更改工单类型：{previous_label} → {target_label}（{payload.reason}）",
            actor_id=actor_id,
            details={
                "from_ticket_type": previous,
                "to_ticket_type": target,
                "reason": payload.reason,
            },
        )
        db.flush()
        return ticket

    @classmethod
    def update_replacement(cls, db: Session, ticket: ServiceTicket, payload, *, actor_id: int) -> ServiceTicket:
        if ticket.ticket_type != "replacement":
            raise BusinessError(
                "置换业务信息仅适用于置换工单",
                code="replacement_ticket_required",
                status_code=409,
            )
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise BusinessError(
                "请至少填写一项置换业务信息",
                code="replacement_update_empty",
                status_code=400,
            )
        for field, value in changes.items():
            setattr(ticket, field, value)
        cls._timeline(
            db,
            ticket,
            "replacement_updated",
            "更新置换业务信息",
            actor_id=actor_id,
            details={
                "replacement_inspection_result": ticket.replacement_inspection_result,
                "trade_in_credit": str(ticket.trade_in_credit) if ticket.trade_in_credit is not None else None,
                "return_reference": ticket.return_reference,
                "outbound_to_customer_tracking_no": ticket.outbound_to_customer_tracking_no,
            },
        )
        db.flush()
        return ticket

    @classmethod
    def add_collaborator(cls, db: Session, ticket: ServiceTicket, payload, *, actor_id: int):
        cls._active_user(db, payload.user_id, code="collaborator_not_found", label="协助成员")
        existing = db.scalar(select(ServiceTicketCollaborator).where(
            ServiceTicketCollaborator.ticket_id == ticket.id,
            ServiceTicketCollaborator.user_id == payload.user_id,
        ))
        if existing:
            return existing
        collaborator = ServiceTicketCollaborator(
            ticket_id=ticket.id, user_id=payload.user_id,
            collaborator_role=payload.collaborator_role, added_by=actor_id,
        )
        db.add(collaborator)
        cls._timeline(db, ticket, "collaborator_added", f"添加协助成员 #{payload.user_id}", actor_id=actor_id)
        db.flush()
        return collaborator

    @classmethod
    def add_note(cls, db: Session, ticket: ServiceTicket, payload, *, actor_id: int) -> ServiceTicketNote:
        note = ServiceTicketNote(
            ticket_id=ticket.id, visibility=payload.visibility,
            content=payload.content, author_id=actor_id,
        )
        db.add(note)
        db.flush()
        cls._timeline(
            db, ticket, "note_added",
            "新增客户可见备注" if payload.visibility == "customer" else "新增内部备注",
            actor_id=actor_id, details={"note_id": note.id, "visibility": payload.visibility},
        )
        return note

    @classmethod
    def remind(cls, db: Session, ticket: ServiceTicket, *, actor_id: int, reason: str) -> ServiceTicket:
        ticket.reminder_count += 1
        ticket.last_reminded_at = utcnow()
        cls._timeline(db, ticket, "reminded", f"催办：{reason}", actor_id=actor_id)
        db.flush()
        return ticket

    @classmethod
    def record_reminder_notification(
        cls,
        db: Session,
        ticket: ServiceTicket,
        *,
        actor_id: int,
        result: dict,
    ) -> None:
        status = result.get("status", "unknown")
        labels = {
            "sent": "企业微信催办已发送",
            "mock": "企业微信催办已模拟",
            "skipped": "企业微信催办未发送",
            "failed": "企业微信催办发送失败",
        }
        safe_details = {
            key: value
            for key, value in result.items()
            if key not in {"preview", "access_token", "secret"}
        }
        cls._timeline(
            db,
            ticket,
            "reminder_notification",
            labels.get(status, "企业微信催办状态已更新"),
            actor_id=actor_id,
            details=safe_details,
        )
        db.flush()

    @classmethod
    def update_description(cls, db: Session, ticket: ServiceTicket, payload, *, actor_id: int) -> ServiceTicket:
        next_description = payload.description.strip()
        if next_description == ticket.description:
            return ticket
        previous_description = ticket.description
        revision_count = db.scalar(
            select(func.count()).select_from(ServiceTicketTimeline).where(
                ServiceTicketTimeline.ticket_id == ticket.id,
                ServiceTicketTimeline.event_type == "description_updated",
            )
        ) or 0
        version = revision_count + 2
        ticket.description = next_description
        cls._timeline(
            db,
            ticket,
            "description_updated",
            f"更新问题描述：{payload.reason}",
            actor_id=actor_id,
            details={
                "version": version,
                "previous_description": previous_description,
                "description": next_description,
                "reason": payload.reason,
            },
        )
        db.flush()
        return ticket

    @classmethod
    def create_escalation(cls, db: Session, payload, *, actor_id: int) -> SpecialistEscalation:
        ticket = db.get(ServiceTicket, payload.service_ticket_id)
        if not ticket:
            raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
        if not payload.assigned_specialist_id and not payload.specialist_group_id:
            raise BusinessError("必须指定高级专员或专员组", code="specialist_target_required")
        cls._active_user(db, payload.assigned_specialist_id, code="specialist_not_found", label="高级专员")
        group = cls._active_group(
            db, payload.specialist_group_id,
            code="specialist_group_not_found", label="专员组",
        )
        if group and group.group_type != "specialist":
            raise BusinessError("所选处理组不是专员组", code="specialist_group_invalid", status_code=409)
        if ticket.status in TERMINAL_TICKET_STATUSES:
            raise BusinessError("已结束的服务工单不能发起专员升级", code="ticket_terminal_state", status_code=409)
        escalation = SpecialistEscalation(
            escalation_no=make_no("ESC"),
            service_ticket_id=ticket.id,
            repair_order_id=ticket.repair_order_id,
            reason=payload.reason,
            problem_summary=payload.problem_summary,
            attempted_solutions=payload.attempted_solutions,
            urgency=payload.urgency,
            assigned_specialist_id=payload.assigned_specialist_id,
            specialist_group_id=payload.specialist_group_id,
            created_by=actor_id,
        )
        db.add(escalation)
        ticket.status = "waiting_internal"
        db.flush()
        cls._timeline(
            db, ticket, "escalated", f"提交高级专员：{escalation.escalation_no}",
            actor_id=actor_id, details={"escalation_id": escalation.id},
        )
        return escalation

    @classmethod
    def update_escalation(cls, db: Session, escalation: SpecialistEscalation, payload, *, actor_id: int) -> SpecialistEscalation:
        previous_status = escalation.status
        if payload.status != previous_status and payload.status not in ESCALATION_TRANSITIONS.get(previous_status, set()):
            raise BusinessError(
                f"专员升级状态不能从 {previous_status} 变更为 {payload.status}",
                code="invalid_escalation_transition",
                status_code=409,
            )
        if payload.status == "returned" and not payload.return_reason:
            raise BusinessError("退回时必须填写补充材料要求", code="return_reason_required")
        if payload.status == "completed" and (not payload.solution or not payload.final_result):
            raise BusinessError("完成升级时必须填写解决方案和最终结果", code="escalation_result_required")
        now = utcnow()
        escalation.status = payload.status
        if payload.status == "accepted":
            escalation.accepted_at = now
        elif payload.status == "returned":
            escalation.returned_at = now
        elif payload.status == "completed":
            escalation.completed_at = now
        for field in ("return_reason", "specialist_opinion", "solution", "final_result"):
            value = getattr(payload, field)
            if value is not None:
                setattr(escalation, field, value)
        ticket = db.get(ServiceTicket, escalation.service_ticket_id)
        if ticket:
            if payload.status in {"accepted", "in_progress"}:
                ticket.status = "in_progress"
            elif payload.status == "returned":
                ticket.status = "waiting_internal"
            cls._timeline(
                db, ticket, "escalation_updated",
                f"高级专员流程更新为 {payload.status}", actor_id=actor_id,
                details={"escalation_id": escalation.id},
            )
        db.flush()
        return escalation
