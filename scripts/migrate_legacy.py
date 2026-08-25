from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import BASE_DIR
from app.core.database import SessionLocal, create_schema
from app.models.entities import Attachment, Customer, DroneDevice, Quote, QuoteItem, RepairOrder, RepairOrderStatusHistory, SystemSetting


def decimal_value(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def meaningful_serial(value: str | None) -> bool:
    return bool(value and value.strip().lower() not in {"无", "查询", "未知", "none", "n/a"})


def migrate(source: Path, *, force: bool = False) -> dict:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    create_schema()
    backup_dir = BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f"{source.stem}-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(source, backup)
    report = {"source": str(source), "backup": str(backup), "started_at": datetime.now(timezone.utc).isoformat(), "imported": {"customers": 0, "devices": 0, "orders": 0, "quotes": 0, "items": 0, "attachments": 0}, "skipped": [], "warnings": []}
    with sqlite3.connect(source) as legacy:
        legacy.row_factory = sqlite3.Row
        tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "quotations" not in tables:
            raise RuntimeError("旧数据库中没有 quotations 表")
        rows = legacy.execute("SELECT * FROM quotations ORDER BY id").fetchall()
    with SessionLocal() as db:
        marker = db.scalar(select(SystemSetting).where(SystemSetting.key == "legacy_quotation_migration_source"))
        if marker and not force:
            report["warnings"].append("该旧库已迁移；未重复执行。需要重跑时使用 --force。")
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            return report
        for row in rows:
            data = dict(row)
            quote_no = str(data.get("quote_id") or f"LEGACY-{data['id']}")
            if db.scalar(select(Quote).where(Quote.quote_no == quote_no)):
                report["skipped"].append({"quote_no": quote_no, "reason": "目标库已存在"})
                continue
            phone = (data.get("phone") or "").strip() or None
            email = (data.get("customer_email") or "").strip() or None
            name = (data.get("customer_name") or "").strip() or "历史客户"
            customer = db.scalar(select(Customer).where(Customer.phone == phone)) if phone else None
            if not customer:
                customer = db.scalar(select(Customer).where(Customer.name == name, Customer.phone.is_(None)))
            if not customer:
                customer = Customer(customer_no=f"LEG-CU-{data['id']:06d}", name=name, phone=phone, email=email, notes="由旧报价库迁移")
                db.add(customer); db.flush(); report["imported"]["customers"] += 1
            elif email and not customer.email:
                customer.email = email
            serial = (data.get("sn") or "").strip()
            actual_serial = serial if meaningful_serial(serial) else f"TEMP-{quote_no}"
            device = DroneDevice(
                customer_id=customer.id, brand="DJI", model=(data.get("model") or "历史设备"),
                serial_number=actual_serial, is_temporary=not meaningful_serial(serial),
                remarks="由旧报价库迁移；独立设备归属记录",
            )
            db.add(device); db.flush(); report["imported"]["devices"] += 1
            order = RepairOrder(order_no=f"LEG-{quote_no}", customer_id=customer.id, device_id=device.id, fault_description=(data.get("reason") or "历史报价"), status="completed" if data.get("status") == "completed" else "pending_quote", total_quote_amount=decimal_value(data.get("grand_total")), customer_notes=data.get("remark"), internal_notes="从旧 quotations 表迁移；报价不等同于收款，未生成财务收入。")
            db.add(order); db.flush(); db.add(RepairOrderStatusHistory(repair_order_id=order.id, from_status=None, to_status=order.status, reason="旧库迁移")); report["imported"]["orders"] += 1
            quote = Quote(quote_no=quote_no, repair_order_id=order.id, version=1, status="confirmed" if data.get("status") == "completed" else "draft", labor_fee=decimal_value(data.get("labor_price")), subtotal=decimal_value(data.get("parts_total")), total_amount=decimal_value(data.get("grand_total")))
            db.add(quote); db.flush(); report["imported"]["quotes"] += 1
            try:
                parts = json.loads(data.get("parts_json") or "[]")
            except Exception as exc:
                parts = []; report["warnings"].append(f"{quote_no}: parts_json 无法解析: {exc}")
            for index, item in enumerate(parts):
                if isinstance(item, list):
                    name_part, unit_price, qty = item[0], item[1], 1
                else:
                    name_part, unit_price, qty = item.get("name", "历史项目"), item.get("price", 0), item.get("qty", 1)
                quantity, price = Decimal(str(qty or 1)), decimal_value(unit_price)
                db.add(QuoteItem(quote_id=quote.id, item_name=str(name_part), quantity=quantity, unit_price=price, cost_price=Decimal("0.00"), amount=(quantity * price).quantize(Decimal("0.01")), item_type="part", sort_order=index)); report["imported"]["items"] += 1
            if quote.labor_fee:
                db.add(QuoteItem(quote_id=quote.id, item_name=data.get("labor_type") or "人工服务", quantity=Decimal("1"), unit_price=quote.labor_fee, cost_price=Decimal("0"), amount=quote.labor_fee, item_type="labor", sort_order=len(parts))); report["imported"]["items"] += 1
            pdf_path = Path(str(data.get("pdf_path") or ""))
            if pdf_path and not pdf_path.is_absolute():
                pdf_path = (BASE_DIR / pdf_path).resolve()
            if pdf_path.is_file():
                content = pdf_path.read_bytes()
                import hashlib
                db.add(Attachment(customer_id=customer.id, repair_order_id=order.id, attachment_type="legacy_quote_pdf", original_filename=pdf_path.name, storage_path=str(pdf_path), content_type="application/pdf", file_size=len(content), sha256=hashlib.sha256(content).hexdigest())); report["imported"]["attachments"] += 1
            elif data.get("pdf_path"):
                report["warnings"].append(f"{quote_no}: PDF 文件缺失: {data.get('pdf_path')}")
        if marker:
            marker.value = str(source)
        else:
            db.add(SystemSetting(key="legacy_quotation_migration_source", value=str(source), description="旧 quotations 数据库迁移来源"))
        db.commit()
    known_pdf_names = {Path(str(row["pdf_path"] or "")).name for row in rows}
    for path in (BASE_DIR / "records").glob("*.pdf"):
        if path.name not in known_pdf_names:
            report["warnings"].append(f"孤立历史 PDF（未在 quotations 表中登记）: {path.name}")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_dir = BASE_DIR / "migration_reports"; report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"legacy-migration-{datetime.now():%Y%m%d-%H%M%S}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="备份并迁移旧 quotation.db")
    parser.add_argument("--source", type=Path, default=BASE_DIR / "quotation.db")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, force=args.force), ensure_ascii=False, indent=2))
