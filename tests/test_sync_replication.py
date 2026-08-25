from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models.entities import (
    Customer,
    DroneDevice,
    FinanceTransaction,
    InventoryItem,
    InventoryTransaction,
    Quote,
    RepairOrder,
    ServiceTicket,
    SyncCanonicalRecord,
    SyncConflict,
    SyncEntityState,
    SyncNode,
    SyncOutboxEvent,
    SyncServerChange,
    SystemSetting,
)
from app.services.sync import (
    _quote_payload,
    _ticket_payload,
    apply_changes,
    apply_payload,
    collect_local_changes,
    payload_hash,
    pending_events,
    receive_events,
)


def _customer_payload(name: str) -> dict:
    return {
        "customer_no": "CU-SYNC-001",
        "name": name,
        "phone": "13400000001",
        "email": None,
        "wechat": None,
        "wecom_external_user_id": None,
        "wecom_group_id": None,
        "customer_type": "individual",
        "company_name": None,
        "province": None,
        "city": None,
        "address": None,
        "notes": None,
        "deleted_at": None,
        "deletion_batch_id": None,
    }


def _change(payload: dict, revision: int) -> dict:
    return {
        "server_seq": revision,
        "event_id": str(uuid.uuid4()),
        "origin_node_id": str(uuid.uuid4()),
        "entity_type": "customer",
        "record_key": payload["customer_no"],
        "operation": "upsert",
        "revision": revision,
        "payload_hash": payload_hash(payload),
        "payload_json": payload,
    }


def _incoming_event(
    payload: dict,
    *,
    entity_type: str = "customer",
    record_key: str | None = None,
    operation: str = "upsert",
    base_revision: int = 0,
    declared_hash: str | None = None,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "entity_type": entity_type,
        "record_key": record_key if record_key is not None else payload.get("customer_no", "UNKNOWN"),
        "operation": operation,
        "base_revision": base_revision,
        "base_payload_json": None,
        "payload_json": payload,
        "payload_hash": declared_hash if declared_hash is not None else payload_hash(payload),
    }


def _assert_sync_push_left_no_rows(db: Session) -> None:
    assert list(db.scalars(select(Customer))) == []
    assert list(db.scalars(select(SyncCanonicalRecord))) == []
    assert list(db.scalars(select(SyncServerChange))) == []
    assert list(db.scalars(select(SyncNode))) == []
    assert list(db.scalars(select(SystemSetting))) == []


def test_terminal_pull_applies_canonical_record_and_tracks_revision(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'terminal.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        payload = _customer_payload("主机下发客户")
        result = apply_changes(db, [_change(payload, 1)])
        assert result == {"applied": 1, "conflicts": 0}
        customer = db.scalar(select(Customer).where(Customer.customer_no == "CU-SYNC-001"))
        assert customer is not None
        assert customer.name == "主机下发客户"
        state = db.scalar(select(SyncEntityState).where(
            SyncEntityState.entity_type == "customer",
            SyncEntityState.record_key == "CU-SYNC-001",
        ))
        assert state is not None
        assert state.server_revision == 1


def test_terminal_pull_preserves_unsent_local_edit_as_conflict(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'terminal.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        initial = _customer_payload("初始客户")
        apply_changes(db, [_change(initial, 1)])
        customer = db.scalar(select(Customer).where(Customer.customer_no == "CU-SYNC-001"))
        customer.name = "终端未发送修改"
        db.commit()

        remote = _customer_payload("主机同时修改")
        result = apply_changes(db, [_change(remote, 2)])
        assert result == {"applied": 0, "conflicts": 1}
        assert customer.name == "终端未发送修改"
        conflict = db.scalar(select(SyncConflict))
        assert conflict is not None
        assert "name" in conflict.conflicting_fields_json


def test_terminal_force_host_resolution_closes_local_outbox(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'terminal.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        initial = _customer_payload("初始客户")
        apply_changes(db, [_change(initial, 1)])
        customer = db.scalar(select(Customer).where(Customer.customer_no == "CU-SYNC-001"))
        customer.name = "终端冲突修改"
        event_id = str(uuid.uuid4())
        db.add(SyncOutboxEvent(
            event_id=event_id,
            origin_node_id=str(uuid.uuid4()),
            entity_type="customer",
            record_key=initial["customer_no"],
            base_revision=1,
            base_payload_json=initial,
            payload_json=_customer_payload("终端冲突修改"),
            payload_hash=payload_hash(_customer_payload("终端冲突修改")),
            status="conflict",
        ))
        db.commit()

        host = _customer_payload("主机最终版本")
        change = _change(host, 2)
        change["event_id"] = event_id
        change["operation"] = "force_host"
        result = apply_changes(db, [change])
        assert result == {"applied": 1, "conflicts": 0}
        assert customer.name == "主机最终版本"
        outbox = db.scalar(select(SyncOutboxEvent).where(SyncOutboxEvent.event_id == event_id))
        assert outbox.status == "acknowledged"


def test_initial_inventory_history_import_does_not_double_apply_stock(tmp_path):
    from decimal import Decimal

    terminal_engine = create_engine(f"sqlite:///{(tmp_path / 'terminal.db').as_posix()}")
    host_engine = create_engine(f"sqlite:///{(tmp_path / 'host.db').as_posix()}")
    Base.metadata.create_all(terminal_engine)
    Base.metadata.create_all(host_engine)
    sync_settings = receive_events.__globals__["settings"]
    original_role = sync_settings.sync_role
    original_node_id = sync_settings.sync_node_id
    try:
        object.__setattr__(sync_settings, "sync_role", "terminal")
        object.__setattr__(sync_settings, "sync_node_id", str(uuid.uuid4()))
        with Session(terminal_engine) as db:
            item = InventoryItem(
                sku="BOOTSTRAP-001",
                name="首次同步物料",
                stock_quantity=Decimal("3"),
                purchase_price=Decimal("10"),
                sale_price=Decimal("20"),
                safety_stock=Decimal("0"),
            )
            db.add(item)
            db.flush()
            db.add(InventoryTransaction(
                transaction_no="ST-BOOTSTRAP-001",
                inventory_item_id=item.id,
                transaction_type="repair_issue",
                quantity=Decimal("2"),
                before_quantity=Decimal("5"),
                after_quantity=Decimal("3"),
                unit_cost=Decimal("10"),
            ))
            db.commit()
            collect_local_changes(db)
            events = pending_events(db)
            tx_event = next(event for event in events if event.entity_type == "inventory_transaction")
            assert tx_event.operation == "history_import"
            origin_node_id = tx_event.origin_node_id
            payload = [{
                "event_id": event.event_id,
                "entity_type": event.entity_type,
                "record_key": event.record_key,
                "operation": event.operation,
                "base_revision": event.base_revision,
                "base_payload_json": event.base_payload_json,
                "payload_json": event.payload_json,
                "payload_hash": event.payload_hash,
            } for event in events]

        object.__setattr__(sync_settings, "sync_role", "host")
        object.__setattr__(sync_settings, "sync_node_id", str(uuid.uuid4()))
        with Session(host_engine) as db:
            result = receive_events(db, origin_node_id, payload)
            assert result["conflicts"] == []
            item = db.scalar(select(InventoryItem).where(InventoryItem.sku == "BOOTSTRAP-001"))
            assert item.stock_quantity == Decimal("3.000")
            tx = db.scalar(select(InventoryTransaction).where(
                InventoryTransaction.transaction_no == "ST-BOOTSTRAP-001"
            ))
            assert tx.before_quantity == Decimal("5.000")
            assert tx.after_quantity == Decimal("3.000")
    finally:
        object.__setattr__(sync_settings, "sync_role", original_role)
        object.__setattr__(sync_settings, "sync_node_id", original_node_id)


def test_host_prevalidates_entire_malformed_batch_before_any_write(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'host-malformed-batches.db').as_posix()}")
    Base.metadata.create_all(engine)
    sync_settings = receive_events.__globals__["settings"]
    original_role = sync_settings.sync_role
    try:
        object.__setattr__(sync_settings, "sync_role", "host")
        valid = _customer_payload("本应创建但必须整体拒绝")
        valid["customer_no"] = "CU-BATCH-VALID"
        bad_hash = _customer_payload("哈希错误")
        bad_hash["customer_no"] = "CU-BATCH-BAD-HASH"
        key_mismatch = _customer_payload("业务键不一致")
        key_mismatch["customer_no"] = "CU-ACTUAL"
        cases = [
            (
                [
                    _incoming_event(valid),
                    _incoming_event(bad_hash, declared_hash="0" * 64),
                ],
                "sync_payload_hash_mismatch",
            ),
            (
                [_incoming_event(key_mismatch, record_key="CU-CLAIMED")],
                "sync_record_key_mismatch",
            ),
            (
                [_incoming_event(_customer_payload("非法实体"), entity_type="unknown_entity")],
                "invalid_sync_entity_type",
            ),
            (
                [_incoming_event(_customer_payload("非法操作"), operation="force_host")],
                "invalid_sync_operation",
            ),
            (
                [_incoming_event(_customer_payload("错误历史导入"), operation="history_import")],
                "invalid_sync_history_import",
            ),
            (
                [_incoming_event(_customer_payload("不存在的非零基础版本"), base_revision=3)],
                "sync_base_revision_without_record",
            ),
        ]
        for events, expected_code in cases:
            with Session(engine) as db:
                with pytest.raises(BusinessError) as caught:
                    receive_events(db, str(uuid.uuid4()), events)
                assert caught.value.status_code == 422
                assert caught.value.code == expected_code
                _assert_sync_push_left_no_rows(db)
    finally:
        object.__setattr__(sync_settings, "sync_role", original_role)
        engine.dispose()


def test_host_accepts_valid_event_and_preserves_event_id_replay_semantics(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'host-valid-replay.db').as_posix()}")
    Base.metadata.create_all(engine)
    sync_settings = receive_events.__globals__["settings"]
    original_role = sync_settings.sync_role
    try:
        object.__setattr__(sync_settings, "sync_role", "host")
        payload = _customer_payload("合法同步客户")
        event = _incoming_event(payload)
        origin_node_id = str(uuid.uuid4())
        with Session(engine) as db:
            first = receive_events(db, origin_node_id, [event])
            replay = receive_events(db, origin_node_id, [event])
            assert first["conflicts"] == []
            assert first["acknowledgements"] == [{
                "event_id": event["event_id"],
                "revision": 1,
                "duplicate": False,
            }]
            assert replay["conflicts"] == []
            assert replay["acknowledgements"] == [{
                "event_id": event["event_id"],
                "revision": 1,
                "duplicate": True,
            }]
            assert len(list(db.scalars(select(Customer)))) == 1
    finally:
        object.__setattr__(sync_settings, "sync_role", original_role)
        engine.dispose()


def test_sync_recomputes_quote_money_and_rejects_cross_customer_links(tmp_path):
    from decimal import Decimal

    engine = create_engine(f"sqlite:///{(tmp_path / 'sync-domain-invariants.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        customer = Customer(customer_no="CU-SYNC-A", name="同步客户 A")
        other_customer = Customer(customer_no="CU-SYNC-B", name="同步客户 B")
        db.add_all([customer, other_customer])
        db.flush()
        device = DroneDevice(
            sync_key="DEV-SYNC-A",
            customer_id=customer.id,
            brand="DJI",
            model="Air 3S",
            serial_number="SYNC-A",
        )
        other_device = DroneDevice(
            sync_key="DEV-SYNC-B",
            customer_id=other_customer.id,
            brand="DJI",
            model="Mini 4 Pro",
            serial_number="SYNC-B",
        )
        db.add_all([device, other_device])
        db.flush()
        order = RepairOrder(
            order_no="R-260811-00A1",
            customer_id=customer.id,
            device_id=device.id,
            fault_description="同步报价校验",
        )
        db.add(order)
        db.commit()

        quote_payload = {
            "quote_no": "QT-SYNC-CALC",
            "order_no": order.order_no,
            "ticket_no": None,
            "version": 1,
            "status": "draft",
            "subtotal": "999.00",
            "discount": "3.00",
            "labor_fee": "5.00",
            "shipping_fee": "1.00",
            "total_amount": "777.00",
            "assessment_result": None,
            "assessment_responsibility": None,
            "repair_recommendation": None,
            "customer_notice": None,
            "payment_url": "https://pay.example.com/quotes/QT-SYNC-CALC?channel=offline%20node",
            "customer_confirmed_at": None,
            "deleted_at": None,
            "deletion_batch_id": None,
            "items": [{
                "item_name": "同步配件",
                "specification": None,
                "quantity": "2",
                "unit_price": "10",
                "cost_price": "4",
                "amount": "999",
                "item_type": "part",
                "remarks": None,
                "sort_order": 0,
            }],
        }
        apply_payload(db, "quote", quote_payload, host_merge=True)
        db.commit()
        quote = db.scalar(select(Quote).where(Quote.quote_no == "QT-SYNC-CALC"))
        db.refresh(order)
        assert quote.subtotal == Decimal("20.00")
        assert quote.total_amount == Decimal("23.00")
        assert quote.items[0].amount == Decimal("20.00")
        assert order.total_quote_amount == Decimal("23.00")
        assert quote.payment_url == quote_payload["payment_url"]
        assert _quote_payload(db, quote)["payment_url"] == quote_payload["payment_url"]

        # Older nodes do not know the field. Their payload must not erase the
        # quote-version payment link already stored by a newer node.
        legacy_quote_payload = {**quote_payload, "status": "sent"}
        legacy_quote_payload.pop("payment_url")
        apply_payload(db, "quote", legacy_quote_payload, host_merge=True)
        db.commit()
        db.refresh(quote)
        assert quote.status == "sent"
        assert quote.payment_url == quote_payload["payment_url"]

        replacement_payload = {
            "ticket_no": "TKT-SYNC-REPLACEMENT",
            "ticket_type": "replacement",
            "title": "同步置换业务",
            "description": "旧机抵折并寄送新机",
            "status": "open",
            "priority": "normal",
            "customer_no": customer.customer_no,
            "device_key": device.sync_key,
            "order_no": None,
            "current_owner": None,
            "processing_group": None,
            "created_by": None,
            "due_at": None,
            "first_response_at": None,
            "resolved_at": None,
            "closed_at": None,
            "last_reminded_at": None,
            "reminder_count": 0,
            "replacement_inspection_result": "外观划痕，核心功能正常",
            "trade_in_credit": "1688.50",
            "return_reference": "线下交易：同步节点门店交接",
            "outbound_to_customer_tracking_no": "SF-SYNC-OUT-001",
            "deleted_at": None,
            "deletion_batch_id": None,
            "collaborators": [],
            "notes": [],
            "timeline": [],
        }
        apply_payload(db, "service_ticket", replacement_payload, host_merge=True)
        db.commit()
        replacement = db.scalar(select(ServiceTicket).where(
            ServiceTicket.ticket_no == replacement_payload["ticket_no"]
        ))
        assert replacement.ticket_type == "replacement"
        assert replacement.replacement_inspection_result == replacement_payload["replacement_inspection_result"]
        assert replacement.trade_in_credit == Decimal("1688.50")
        assert replacement.return_reference == replacement_payload["return_reference"]
        assert replacement.outbound_to_customer_tracking_no == replacement_payload["outbound_to_customer_tracking_no"]
        replacement_export = _ticket_payload(db, replacement)
        for field in (
            "replacement_inspection_result",
            "trade_in_credit",
            "return_reference",
            "outbound_to_customer_tracking_no",
        ):
            assert replacement_export[field] == replacement_payload[field]

        # A payload produced by a pre-replacement-fields node must update its
        # known fields without erasing the newer host's置换业务资料。
        legacy_ticket_payload = {**replacement_payload, "title": "旧节点更新后的置换标题"}
        for field in (
            "replacement_inspection_result",
            "trade_in_credit",
            "return_reference",
            "outbound_to_customer_tracking_no",
        ):
            legacy_ticket_payload.pop(field)
        apply_payload(db, "service_ticket", legacy_ticket_payload, host_merge=True)
        db.commit()
        db.refresh(replacement)
        assert replacement.title == "旧节点更新后的置换标题"
        assert replacement.replacement_inspection_result == replacement_payload["replacement_inspection_result"]
        assert replacement.trade_in_credit == Decimal("1688.50")
        assert replacement.return_reference == replacement_payload["return_reference"]
        assert replacement.outbound_to_customer_tracking_no == replacement_payload["outbound_to_customer_tracking_no"]

        replacement_quote_payload = {
            **quote_payload,
            "quote_no": "QT-SYNC-REPLACEMENT",
            "order_no": None,
            "ticket_no": replacement.ticket_no,
            "discount": "1688.50",
            "labor_fee": "100.00",
            "shipping_fee": "50.00",
            "payment_url": "https://pay.example.com/replacement/QT-SYNC-REPLACEMENT",
            "items": [{
                **quote_payload["items"][0],
                "item_name": "同步置换新机方案",
                "quantity": "2",
                "unit_price": "2000.00",
                "cost_price": "1500.00",
            }],
        }
        apply_payload(db, "quote", replacement_quote_payload, host_merge=True)
        db.commit()
        replacement_quote = db.scalar(select(Quote).where(
            Quote.quote_no == replacement_quote_payload["quote_no"]
        ))
        assert replacement_quote.service_ticket_id == replacement.id
        assert replacement_quote.subtotal == Decimal("4000.00")
        assert replacement_quote.discount == Decimal("1688.50")
        # The synchronized quote discount includes the trade-in credit once;
        # ServiceTicket.trade_in_credit must not be subtracted implicitly again.
        assert replacement_quote.total_amount == Decimal("2461.50")
        assert _quote_payload(db, replacement_quote)["payment_url"] == replacement_quote_payload["payment_url"]

        changed_type_payload = {**replacement_payload, "ticket_type": "retail"}
        for field in (
            "replacement_inspection_result",
            "trade_in_credit",
            "return_reference",
            "outbound_to_customer_tracking_no",
        ):
            changed_type_payload[field] = None
        with pytest.raises(ValueError, match="已有有效报价"):
            apply_payload(db, "service_ticket", changed_type_payload, host_merge=True)
        db.rollback()
        db.refresh(replacement)
        assert replacement.ticket_type == "replacement"

        invalid_replacement_fields = {
            **replacement_payload,
            "ticket_no": "TKT-SYNC-NON-REPLACEMENT",
            "ticket_type": "consultation",
        }
        with pytest.raises(ValueError, match="置换业务字段仅适用于置换工单"):
            apply_payload(db, "service_ticket", invalid_replacement_fields, host_merge=True)
        db.rollback()
        assert db.scalar(select(ServiceTicket).where(
            ServiceTicket.ticket_no == invalid_replacement_fields["ticket_no"]
        )) is None

        for index, invalid_amount in enumerate(("1.234", "10000000000.00", "NaN", "not-a-number")):
            invalid_amount_payload = {
                **replacement_payload,
                "ticket_no": f"TKT-SYNC-BAD-AMOUNT-{index}",
                "trade_in_credit": invalid_amount,
            }
            with pytest.raises(ValueError, match="置换业务字段格式无效"):
                apply_payload(db, "service_ticket", invalid_amount_payload, host_merge=True)
            db.rollback()
            assert db.scalar(select(ServiceTicket).where(
                ServiceTicket.ticket_no == invalid_amount_payload["ticket_no"]
            )) is None

        invalid_quote = {**quote_payload, "quote_no": "QT-SYNC-NEGATIVE"}
        invalid_quote["items"] = [{**quote_payload["items"][0], "unit_price": "-1"}]
        with pytest.raises(ValueError, match="报价项数量和价格无效"):
            apply_payload(db, "quote", invalid_quote, host_merge=True)
        db.rollback()
        assert db.scalar(select(Quote).where(Quote.quote_no == "QT-SYNC-NEGATIVE")) is None

        cross_order_payload = {
            "order_no": "R-260811-00B1",
            "customer_no": customer.customer_no,
            "device_key": other_device.sync_key,
            "fault_description": "不允许跨客户设备",
        }
        with pytest.raises(ValueError, match="设备与客户归属不一致"):
            apply_payload(db, "repair_order", cross_order_payload, host_merge=True)
        db.rollback()

        cross_ticket_payload = {
            "ticket_no": "TKT-SYNC-CROSS",
            "ticket_type": "repair",
            "title": "跨客户服务工单",
            "description": "不得绑定",
            "customer_no": other_customer.customer_no,
            "device_key": other_device.sync_key,
            "order_no": order.order_no,
        }
        with pytest.raises(ValueError, match="客户与维修工单不一致"):
            apply_payload(db, "service_ticket", cross_ticket_payload, host_merge=True)
        db.rollback()
        assert db.scalar(select(ServiceTicket).where(ServiceTicket.ticket_no == "TKT-SYNC-CROSS")) is None

        finance_payload = {
            "transaction_no": "FN-SYNC-CROSS",
            "order_no": order.order_no,
            "customer_no": other_customer.customer_no,
            "quote_no": None,
            "transaction_type": "income",
            "category": "维修收款",
            "amount": "1.00",
        }
        with pytest.raises(ValueError, match="客户与维修工单不一致"):
            apply_payload(db, "finance_transaction", finance_payload, host_merge=True)
        db.rollback()
        assert db.scalar(select(FinanceTransaction).where(
            FinanceTransaction.transaction_no == "FN-SYNC-CROSS"
        )) is None
    engine.dispose()


def test_sync_rejects_active_quotes_for_soft_deleted_targets_but_accepts_tombstones(tmp_path):
    """A terminal must not resurrect a quote while its host-side target is deleted."""

    engine = create_engine(f"sqlite:///{(tmp_path / 'sync-deleted-quote-targets.db').as_posix()}")
    Base.metadata.create_all(engine)
    deleted_at = datetime.now(timezone.utc)

    def quote_payload(*, quote_no: str, order_no: str | None = None, ticket_no: str | None = None, deleted: bool = False):
        return {
            "quote_no": quote_no,
            "order_no": order_no,
            "ticket_no": ticket_no,
            "version": 1,
            "status": "superseded" if deleted else "draft",
            "subtotal": "1000.00",
            "discount": "100.00",
            "labor_fee": "50.00",
            "shipping_fee": "10.00",
            "total_amount": "960.00",
            "assessment_result": None,
            "assessment_responsibility": None,
            "repair_recommendation": None,
            "customer_notice": None,
            "payment_url": "https://pay.example.com/sync/deleted-target",
            "customer_confirmed_at": None,
            "deleted_at": deleted_at.isoformat() if deleted else None,
            "deletion_batch_id": "sync-deleted-target-batch" if deleted else None,
            "items": [{
                "item_name": "同步报价项目",
                "specification": None,
                "quantity": "1",
                "unit_price": "1000.00",
                "cost_price": "800.00",
                "amount": "1000.00",
                "item_type": "part",
                "remarks": None,
                "sort_order": 0,
            }],
        }

    with Session(engine) as db:
        customer = Customer(customer_no="CU-SYNC-DELETED-TARGET", name="同步软删目标客户")
        db.add(customer)
        db.flush()
        device = DroneDevice(
            sync_key="DEV-SYNC-DELETED-TARGET",
            customer_id=customer.id,
            brand="DJI",
            model="Mavic 3 Pro",
            serial_number="SYNC-DELETED-TARGET-SN",
        )
        db.add(device)
        db.flush()
        order = RepairOrder(
            order_no="RO-260813-AB12",
            customer_id=customer.id,
            device_id=device.id,
            fault_description="已软删维修工单不得接收活跃报价",
            deleted_at=deleted_at,
            deletion_batch_id="deleted-order-batch",
        )
        retail = ServiceTicket(
            ticket_no="TKT-SYNC-DELETED-RETAIL",
            ticket_type="retail",
            title="已软删零售工单",
            description="不得接收活跃报价",
            customer_id=customer.id,
            deleted_at=deleted_at,
            deletion_batch_id="deleted-retail-batch",
        )
        replacement = ServiceTicket(
            ticket_no="TKT-SYNC-DELETED-REPLACEMENT",
            ticket_type="replacement",
            title="已软删置换工单",
            description="不得接收活跃报价",
            customer_id=customer.id,
            trade_in_credit="100.00",
            deleted_at=deleted_at,
            deletion_batch_id="deleted-replacement-batch",
        )
        db.add_all([order, retail, replacement])
        db.commit()

        targets = (
            ("ORDER", {"order_no": order.order_no}, "活跃报价不能关联已删除的维修工单"),
            ("RETAIL", {"ticket_no": retail.ticket_no}, "活跃报价不能关联已删除的服务工单"),
            ("REPLACEMENT", {"ticket_no": replacement.ticket_no}, "活跃报价不能关联已删除的服务工单"),
        )
        for label, target, expected_message in targets:
            new_quote_no = f"QT-SYNC-DELETED-{label}-NEW"
            with pytest.raises(ValueError, match=expected_message):
                apply_payload(
                    db,
                    "quote",
                    quote_payload(quote_no=new_quote_no, **target),
                    host_merge=True,
                )
            db.rollback()
            assert db.scalar(select(Quote).where(Quote.quote_no == new_quote_no)) is None

            tombstone = Quote(
                quote_no=f"QT-SYNC-DELETED-{label}-EXISTING",
                repair_order_id=order.id if label == "ORDER" else None,
                service_ticket_id=(retail.id if label == "RETAIL" else replacement.id) if label != "ORDER" else None,
                version=9,
                status="superseded",
                deleted_at=deleted_at,
                deletion_batch_id=f"existing-{label.lower()}-quote-batch",
            )
            db.add(tombstone)
            db.commit()
            with pytest.raises(ValueError, match=expected_message):
                apply_payload(
                    db,
                    "quote",
                    quote_payload(quote_no=tombstone.quote_no, **target),
                    host_merge=True,
                )
            db.rollback()
            db.refresh(tombstone)
            assert tombstone.deleted_at is not None
            assert db.scalar(select(Quote).where(
                Quote.quote_no == tombstone.quote_no,
                Quote.deleted_at.is_(None),
            )) is None

            incoming_tombstone_no = f"QT-SYNC-DELETED-{label}-TOMBSTONE"
            apply_payload(
                db,
                "quote",
                quote_payload(quote_no=incoming_tombstone_no, deleted=True, **target),
                host_merge=True,
            )
            db.commit()
            incoming_tombstone = db.scalar(select(Quote).where(Quote.quote_no == incoming_tombstone_no))
            assert incoming_tombstone is not None
            assert incoming_tombstone.deleted_at is not None

        assert db.scalar(select(Quote.id).where(Quote.deleted_at.is_(None)).limit(1)) is None
    engine.dispose()
