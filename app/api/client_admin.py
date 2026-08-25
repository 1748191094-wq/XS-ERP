from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.helpers import ok
from app.core.auth import admin_access
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.models.client import (
    ClientAccount,
    ClientNotification,
    ClientSession,
    ForumCategory,
    ForumComment,
    ForumPost,
    ForumReport,
    Product,
    ProductCategory,
    ProductImage,
    ProductSKU,
    RecycleCatalogItem,
    RecycleRequest,
    RetailOrder,
)
from app.models.entities import InventoryItem, ServiceTicket, User
from app.schemas.client import (
    ClientAccountStatusUpdate,
    ForumCategoryCreate,
    ForumCategoryUpdate,
    ForumModeration,
    ForumReportModeration,
    ProductCategoryWrite,
    ProductPublishWrite,
    ProductSKUWrite,
    ProductWrite,
    RecycleCatalogWrite,
    RecycleQuoteUpdate,
    RetailOrderStatusUpdate,
)
from app.services.client_uploads import save_client_image
from app.services.recycle_catalog_excel import (
    MAX_IMPORT_BYTES,
    build_recycle_catalog_template,
    parse_recycle_catalog_workbook,
)
from app.schemas.domain import StockChange
from app.services.inventory import InventoryService


router = APIRouter(
    prefix="/api/admin/client",
    tags=["client-admin"],
    dependencies=[Depends(admin_access)],
)


def _commit(db: Session, message: str = "数据存在冲突") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(message, code="client_admin_conflict", status_code=409) from exc


def _client_inventory_item(db: Session, item_id: int | None) -> InventoryItem | None:
    if item_id is None:
        return None
    item = db.scalar(
        select(InventoryItem).where(
            InventoryItem.id == item_id,
            InventoryItem.deleted_at.is_(None),
        )
    )
    if not item:
        raise BusinessError("关联库存物料不存在", code="inventory_not_found", status_code=404)
    if not item.enabled or not item.client_visible:
        raise BusinessError(
            "只能关联已启用且允许客户端展示的库存物料",
            code="inventory_not_client_visible",
            status_code=409,
        )
    return item


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ClientAccount).order_by(ClientAccount.created_at.desc()).limit(500)
    )
    return ok(
        [
            {
                "id": row.id,
                "customer_id": row.customer_id,
                "username": row.username,
                "identifier": f"@{row.username}",
                "phone": row.phone,
                "email": row.email,
                "nickname": row.nickname,
                "status": row.status,
                "last_login_at": row.last_login_at,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    )


@router.patch("/accounts/{account_id}/status")
def account_status(
    account_id: int,
    payload: ClientAccountStatusUpdate,
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ClientAccount, account_id)
    if not row:
        raise BusinessError("客户账号不存在", code="client_account_not_found", status_code=404)
    row.status = payload.status
    if payload.status == "disabled":
        db.execute(
            update(ClientSession)
            .where(
                ClientSession.account_id == row.id, ClientSession.revoked_at.is_(None)
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
    db.commit()
    return ok({"id": row.id, "status": row.status})


@router.get("/product-categories")
def product_categories(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ProductCategory)
        .where(ProductCategory.deleted_at.is_(None))
        .order_by(ProductCategory.sort_order, ProductCategory.id)
    )
    return ok(
        [
            {
                "id": row.id,
                "name": row.name,
                "slug": row.slug,
                "sort_order": row.sort_order,
                "enabled": row.enabled,
            }
            for row in rows
        ]
    )


@router.post("/product-categories", status_code=201)
def create_product_category(
    payload: ProductCategoryWrite, db: Session = Depends(get_db)
) -> dict:
    row = ProductCategory(**payload.model_dump())
    db.add(row)
    _commit(db, "商品分类名称或标识已存在")
    db.refresh(row)
    return ok({"id": row.id, **payload.model_dump()})


@router.patch("/product-categories/{category_id}")
def update_product_category(
    category_id: int, payload: ProductCategoryWrite, db: Session = Depends(get_db)
) -> dict:
    row = db.scalar(
        select(ProductCategory).where(
            ProductCategory.id == category_id, ProductCategory.deleted_at.is_(None)
        )
    )
    if not row:
        raise BusinessError("商品分类不存在", code="product_category_not_found", status_code=404)
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    _commit(db, "商品分类名称或标识已存在")
    return ok({"id": row.id, **payload.model_dump()})


@router.get("/products")
def products(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(Product)
        .where(Product.deleted_at.is_(None))
        .options(selectinload(Product.skus), selectinload(Product.images))
        .order_by(Product.created_at.desc())
    )
    return ok(
        [
            {
                "id": row.id,
                "category_id": row.category_id,
                "name": row.name,
                "slug": row.slug,
                "summary": row.summary,
                "status": row.status,
                "featured": row.featured,
                "sku_count": len([sku for sku in row.skus if sku.deleted_at is None]),
                "image_count": len(row.images),
            }
            for row in rows
        ]
    )


@router.post("/products", status_code=201)
def create_product(payload: ProductWrite, db: Session = Depends(get_db)) -> dict:
    if payload.category_id and not db.scalar(
        select(ProductCategory.id).where(
            ProductCategory.id == payload.category_id,
            ProductCategory.deleted_at.is_(None),
        )
    ):
        raise BusinessError("商品分类不存在", code="product_category_not_found", status_code=404)
    row = Product(**payload.model_dump())
    db.add(row)
    _commit(db, "商品标识已存在")
    db.refresh(row)
    return ok({"id": row.id, **payload.model_dump()})


@router.post("/products/publish", status_code=201)
def publish_product(payload: ProductPublishWrite, db: Session = Depends(get_db)) -> dict:
    """Create a sellable product and its first SKU in one transaction."""
    product_data = payload.product.model_dump()
    if payload.product.category_id and not db.scalar(
        select(ProductCategory.id).where(
            ProductCategory.id == payload.product.category_id,
            ProductCategory.deleted_at.is_(None),
        )
    ):
        raise BusinessError("商品分类不存在", code="product_category_not_found", status_code=404)
    if payload.product.status == "published" and not payload.sku.enabled:
        raise BusinessError(
            "立即上架时首个 SKU 必须启用",
            code="published_product_requires_enabled_sku",
            status_code=422,
        )
    inventory_item = _client_inventory_item(db, payload.sku.inventory_item_id)

    product = Product(**product_data)
    db.add(product)
    try:
        db.flush()
        sku = ProductSKU(
            product_id=product.id,
            inventory_item_id=payload.sku.inventory_item_id,
            sku=payload.sku.sku,
            name=payload.sku.name,
            attributes_json=payload.sku.attributes,
            price=payload.sku.price,
            original_price=payload.sku.original_price,
            stock_quantity=(
                int(inventory_item.stock_quantity)
                if inventory_item is not None
                else payload.sku.stock_quantity
            ),
            enabled=payload.sku.enabled,
        )
        db.add(sku)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(
            "商品标识或 SKU 编码已存在",
            code="product_publish_conflict",
            status_code=409,
        ) from exc
    db.refresh(product)
    db.refresh(sku)
    return ok(
        {
            "id": product.id,
            "sku_id": sku.id,
            "status": product.status,
            "name": product.name,
            "slug": product.slug,
        }
    )


@router.get("/products/{product_id}")
def product_detail(product_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(Product)
        .where(Product.id == product_id, Product.deleted_at.is_(None))
        .options(
            selectinload(Product.skus).joinedload(ProductSKU.inventory_item),
            selectinload(Product.images),
        )
    )
    if not row:
        raise BusinessError("商品不存在", code="product_not_found", status_code=404)
    return ok(
        {
            "id": row.id,
            "category_id": row.category_id,
            "name": row.name,
            "slug": row.slug,
            "summary": row.summary,
            "description": row.description,
            "after_sales": row.after_sales,
            "status": row.status,
            "featured": row.featured,
            "skus": [
                {
                    "id": sku.id,
                    "inventory_item_id": sku.inventory_item_id,
                    "sku": sku.sku,
                    "name": sku.name,
                    "attributes": sku.attributes_json or {},
                    "price": sku.price,
                    "original_price": sku.original_price,
                    "stock_quantity": sku.stock_quantity,
                    "reserved_quantity": sku.reserved_quantity,
                    "enabled": sku.enabled,
                    "inventory": (
                        {
                            "id": sku.inventory_item.id,
                            "sku": sku.inventory_item.sku,
                            "name": sku.inventory_item.name,
                            "stock_quantity": int(sku.inventory_item.stock_quantity),
                            "client_visible": sku.inventory_item.client_visible,
                        }
                        if sku.inventory_item
                        else None
                    ),
                }
                for sku in row.skus
                if sku.deleted_at is None
            ],
            "images": [
                {
                    "id": image.id,
                    "url": f"/api/client/products/images/{image.id}",
                    "alt_text": image.alt_text,
                    "sort_order": image.sort_order,
                }
                for image in sorted(row.images, key=lambda item: (item.sort_order, item.id))
            ],
        }
    )


@router.patch("/products/{product_id}")
def update_product(
    product_id: int, payload: ProductWrite, db: Session = Depends(get_db)
) -> dict:
    row = db.scalar(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    if not row:
        raise BusinessError("商品不存在", code="product_not_found", status_code=404)
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    _commit(db, "商品标识已存在")
    return ok({"id": row.id, **payload.model_dump()})


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    current_user: User = Depends(admin_access),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    if not row:
        raise BusinessError("商品不存在", code="product_not_found", status_code=404)
    row.deleted_at = datetime.now(timezone.utc)
    row.deleted_by = current_user.id
    db.commit()
    return ok({"deleted": True})


@router.post("/products/{product_id}/skus", status_code=201)
def create_sku(
    product_id: int, payload: ProductSKUWrite, db: Session = Depends(get_db)
) -> dict:
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    if not product:
        raise BusinessError("商品不存在", code="product_not_found", status_code=404)
    inventory_item = _client_inventory_item(db, payload.inventory_item_id)
    row = ProductSKU(
        product_id=product.id,
        inventory_item_id=payload.inventory_item_id,
        sku=payload.sku,
        name=payload.name,
        attributes_json=payload.attributes,
        price=payload.price,
        original_price=payload.original_price,
        stock_quantity=(
            int(inventory_item.stock_quantity)
            if inventory_item is not None
            else payload.stock_quantity
        ),
        enabled=payload.enabled,
    )
    db.add(row)
    _commit(db, "SKU 编码或库存关联已存在")
    db.refresh(row)
    response = payload.model_dump()
    response["stock_quantity"] = row.stock_quantity
    return ok({"id": row.id, "product_id": row.product_id, **response})


@router.patch("/skus/{sku_id}")
def update_sku(sku_id: int, payload: ProductSKUWrite, db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(ProductSKU).where(ProductSKU.id == sku_id, ProductSKU.deleted_at.is_(None))
    )
    if not row:
        raise BusinessError("SKU 不存在", code="sku_not_found", status_code=404)
    inventory_item = _client_inventory_item(db, payload.inventory_item_id)
    stock_quantity = (
        int(inventory_item.stock_quantity)
        if inventory_item is not None
        else payload.stock_quantity
    )
    if stock_quantity < row.reserved_quantity:
        raise BusinessError("库存不能小于已锁定数量", code="stock_below_reserved", status_code=409)
    values = payload.model_dump()
    values["attributes_json"] = values.pop("attributes")
    values["stock_quantity"] = stock_quantity
    for field, value in values.items():
        setattr(row, field, value)
    _commit(db, "SKU 编码或库存关联已存在")
    response = payload.model_dump()
    response["stock_quantity"] = row.stock_quantity
    return ok({"id": row.id, **response})


@router.delete("/skus/{sku_id}")
def delete_sku(
    sku_id: int,
    current_user: User = Depends(admin_access),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(ProductSKU).where(ProductSKU.id == sku_id, ProductSKU.deleted_at.is_(None))
    )
    if not row:
        raise BusinessError("SKU 不存在", code="sku_not_found", status_code=404)
    if row.reserved_quantity:
        raise BusinessError("SKU 有锁定订单，不能删除", code="sku_has_reservations", status_code=409)
    row.deleted_at = datetime.now(timezone.utc)
    row.deleted_by = current_user.id
    db.commit()
    return ok({"deleted": True})


@router.post("/products/{product_id}/images", status_code=201)
async def upload_product_image(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    if not product:
        raise BusinessError("商品不存在", code="product_not_found", status_code=404)
    content = await file.read(10 * 1024 * 1024 + 1)
    stored = save_client_image(
        filename=file.filename or "product.jpg",
        content_type=file.content_type,
        content=content,
        folder=f"products/{product.id}",
    )
    row = ProductImage(
        product_id=product.id,
        storage_path=stored.storage_path,
        alt_text=product.name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ok({"id": row.id, "url": f"/api/client/products/images/{row.id}"})


@router.delete("/product-images/{image_id}")
def delete_product_image(image_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ProductImage, image_id)
    if not row:
        raise BusinessError("商品图片不存在", code="product_image_not_found", status_code=404)
    # Keep the physical file for recovery/audit; removing the DB row unpublishes it.
    db.delete(row)
    db.commit()
    return ok({"deleted": True})


@router.get("/orders")
def retail_orders(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(RetailOrder)
        .options(selectinload(RetailOrder.items))
        .order_by(RetailOrder.created_at.desc())
        .limit(500)
    )
    return ok(
        [
            {
                "id": row.id,
                "order_no": row.order_no,
                "account_id": row.account_id,
                "customer_id": row.customer_id,
                "status": row.status,
                "total_amount": row.total_amount,
                "payment_provider": row.payment_provider,
                "tracking_no": row.tracking_no,
                "created_at": row.created_at,
                "items": [
                    {
                        "sku_id": item.sku_id,
                        "name": item.product_name,
                        "quantity": item.quantity,
                        "unit_price": item.unit_price,
                    }
                    for item in row.items
                ],
            }
            for row in rows
        ]
    )


@router.patch("/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    payload: RetailOrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    row = db.scalar(
        select(RetailOrder)
        .where(RetailOrder.id == order_id)
        .options(selectinload(RetailOrder.items))
    )
    if not row:
        raise BusinessError("零售订单不存在", code="retail_order_not_found", status_code=404)
    previous = row.status
    if previous == payload.status:
        return ok({"id": row.id, "status": row.status})
    transitions = {
        "pending_payment": {"paid", "cancelled"},
        "paid": {"processing", "refunding"},
        "processing": {"shipped", "refunding"},
        "shipped": {"completed", "refunding"},
        "refunding": {"refunded"},
    }
    if payload.status not in transitions.get(previous, set()):
        raise BusinessError("订单状态不能这样流转", code="invalid_order_transition", status_code=409)
    if payload.status == "shipped" and not payload.tracking_no:
        raise BusinessError("发货必须填写物流单号", code="tracking_no_required", status_code=400)
    if payload.status in {"cancelled", "refunded"}:
        for item in row.items:
            sku = db.get(ProductSKU, item.sku_id)
            if sku:
                sku.reserved_quantity = max(0, sku.reserved_quantity - item.quantity)
    elif payload.status == "completed":
        for item in row.items:
            sku = db.get(ProductSKU, item.sku_id)
            if sku:
                sku.reserved_quantity = max(0, sku.reserved_quantity - item.quantity)
                if sku.inventory_item_id is not None:
                    InventoryService.change_stock(
                        db,
                        StockChange(
                            inventory_item_id=sku.inventory_item_id,
                            transaction_type="stock_out",
                            quantity=item.quantity,
                            operator_id=current_user.id,
                            remarks=f"商城订单 {row.order_no} 完成出库",
                        ),
                    )
                    inventory_item = db.get(InventoryItem, sku.inventory_item_id)
                    sku.stock_quantity = int(inventory_item.stock_quantity)
                else:
                    sku.stock_quantity = max(0, sku.stock_quantity - item.quantity)
    row.status = payload.status
    if payload.tracking_no is not None:
        row.tracking_no = payload.tracking_no
    if payload.payment_reference is not None:
        row.payment_reference = payload.payment_reference
    db.add(
        ClientNotification(
            account_id=row.account_id,
            notification_type="order_status",
            title="订单状态已更新",
            content=f"订单 {row.order_no} 已更新为 {row.status}。",
            resource_type="retail_order",
            resource_id=row.id,
        )
    )
    db.commit()
    return ok({"id": row.id, "status": row.status, "tracking_no": row.tracking_no})


@router.get("/recycle/catalog")
def recycle_catalog(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(RecycleCatalogItem)
        .where(RecycleCatalogItem.deleted_at.is_(None))
        .order_by(RecycleCatalogItem.sort_order, RecycleCatalogItem.id)
    )
    return ok(
        [
            {
                "id": row.id,
                "brand": row.brand,
                "model": row.model,
                "variant": row.variant,
                "reference_price": row.reference_price,
                "enabled": row.enabled,
                "sort_order": row.sort_order,
            }
            for row in rows
        ]
    )


@router.get("/recycle/catalog/import-template")
def recycle_catalog_import_template() -> Response:
    content = build_recycle_catalog_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                'attachment; filename="recycle-catalog-template.xlsx"; '
                "filename*=UTF-8''%E6%97%A7%E6%9C%BA%E6%9C%80%E9%AB%98%E4%BB%B7%E6%A8%A1%E6%9D%BF.xlsx"
            ),
            "Content-Length": str(len(content)),
            "Cache-Control": "no-store",
        },
    )


@router.post("/recycle/catalog/import")
async def import_recycle_catalog(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> dict:
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise BusinessError(
            "仅支持 .xlsx Excel 文件",
            code="recycle_catalog_import_extension_invalid",
            status_code=415,
        )
    content = await file.read(MAX_IMPORT_BYTES + 1)
    rows = parse_recycle_catalog_workbook(content)
    existing_rows = list(
        db.scalars(
            select(RecycleCatalogItem).where(RecycleCatalogItem.deleted_at.is_(None))
        )
    )
    existing: dict[tuple[str, str, str], RecycleCatalogItem] = {}
    for row in existing_rows:
        key = (row.brand.casefold(), row.model.casefold(), (row.variant or "").casefold())
        if key in existing:
            raise BusinessError(
                "当前报价库存在重复的品牌、型号和版本，请先人工整理后再导入",
                code="recycle_catalog_existing_duplicates",
                status_code=409,
            )
        existing[key] = row

    created = 0
    updated = 0
    imported: list[dict] = []
    for import_row in rows:
        values = import_row.payload.model_dump()
        row = existing.get(import_row.key)
        operation = "updated"
        if row is None:
            row = RecycleCatalogItem(**values)
            db.add(row)
            existing[import_row.key] = row
            created += 1
            operation = "created"
        else:
            for field, value in values.items():
                setattr(row, field, value)
            updated += 1
        imported.append(
            {
                "row": import_row.row_number,
                "brand": values["brand"],
                "model": values["model"],
                "variant": values["variant"],
                "operation": operation,
            }
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(
            "批量导入发生数据冲突，本次没有写入任何报价",
            code="recycle_catalog_import_conflict",
            status_code=409,
        ) from exc
    return ok(
        {
            "total": len(rows),
            "created": created,
            "updated": updated,
            "items": imported,
        }
    )


@router.post("/recycle/catalog", status_code=201)
def create_recycle_catalog(
    payload: RecycleCatalogWrite, db: Session = Depends(get_db)
) -> dict:
    row = RecycleCatalogItem(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return ok({"id": row.id, **payload.model_dump()})


@router.patch("/recycle/catalog/{catalog_id}")
def update_recycle_catalog(
    catalog_id: int, payload: RecycleCatalogWrite, db: Session = Depends(get_db)
) -> dict:
    row = db.scalar(
        select(RecycleCatalogItem).where(
            RecycleCatalogItem.id == catalog_id,
            RecycleCatalogItem.deleted_at.is_(None),
        )
    )
    if not row:
        raise BusinessError("回收机型不存在", code="recycle_catalog_not_found", status_code=404)
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    db.commit()
    return ok({"id": row.id, **payload.model_dump()})


@router.get("/recycle/rules")
def recycle_rules() -> dict:
    raise BusinessError(
        "仅旧报价规则已停用；旧机最高价维护和 Excel 批量导入正常可用",
        code="recycle_rules_disabled",
        status_code=410,
    )


@router.post("/recycle/rules", status_code=201)
def create_recycle_rule() -> dict:
    raise BusinessError(
        "仅旧报价规则已停用；旧机最高价维护和 Excel 批量导入正常可用",
        code="recycle_rules_disabled",
        status_code=410,
    )


@router.patch("/recycle/rules/{rule_id}")
def update_recycle_rule(rule_id: int) -> dict:
    raise BusinessError(
        "仅旧报价规则已停用；旧机最高价维护和 Excel 批量导入正常可用",
        code="recycle_rules_disabled",
        status_code=410,
    )


@router.get("/recycle/requests")
def recycle_requests(db: Session = Depends(get_db)) -> dict:
    rows = list(
        db.scalars(select(RecycleRequest).order_by(RecycleRequest.created_at.desc()).limit(500))
    )
    catalog_ids = {row.catalog_item_id for row in rows}
    catalog = {
        row.id: row
        for row in db.scalars(
            select(RecycleCatalogItem).where(RecycleCatalogItem.id.in_(catalog_ids))
        )
    } if catalog_ids else {}
    return ok(
        [
            {
                "id": row.id,
                "request_no": row.request_no,
                "account_id": row.account_id,
                "customer_id": row.customer_id,
                "catalog_item": (
                    {
                        "id": catalog[row.catalog_item_id].id,
                        "brand": catalog[row.catalog_item_id].brand,
                        "model": catalog[row.catalog_item_id].model,
                        "variant": catalog[row.catalog_item_id].variant,
                    }
                    if row.catalog_item_id in catalog
                    else None
                ),
                "maximum_price": row.reference_max,
                "reference_min": row.reference_min,
                "reference_max": row.reference_max,
                "contact_name": (
                    ((row.questionnaire_json or {}).get("contact") or {}).get("name")
                ),
                "contact_phone": (
                    ((row.questionnaire_json or {}).get("contact") or {}).get("phone")
                ),
                "contact_wechat": (
                    ((row.questionnaire_json or {}).get("contact") or {}).get("wechat")
                ),
                "device_condition": (row.questionnaire_json or {}).get("device_condition"),
                "notes": (row.questionnaire_json or {}).get("notes"),
                "staff_quote": row.staff_quote,
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    )


@router.patch("/recycle/requests/{request_id}/quote")
def update_recycle_quote(
    request_id: int, payload: RecycleQuoteUpdate, db: Session = Depends(get_db)
) -> dict:
    row = db.get(RecycleRequest, request_id)
    if not row:
        raise BusinessError("回收申请不存在", code="recycle_request_not_found", status_code=404)
    if row.status in {"completed", "cancelled", "rejected"}:
        raise BusinessError("当前状态不能报价", code="recycle_quote_not_allowed", status_code=409)
    row.staff_quote = payload.staff_quote
    row.status = payload.status
    ticket = db.get(ServiceTicket, row.service_ticket_id) if row.service_ticket_id else None
    if ticket:
        ticket.status = "waiting_customer"
    db.add(
        ClientNotification(
            account_id=row.account_id,
            notification_type="recycle_quote",
            title="回收报价已更新",
            content=f"申请 {row.request_no} 的正式报价为 ¥{row.staff_quote}。",
            resource_type="recycle_request",
            resource_id=row.id,
        )
    )
    db.commit()
    return ok({"id": row.id, "staff_quote": row.staff_quote, "status": row.status})


@router.get("/forum/categories")
def forum_categories(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ForumCategory).order_by(ForumCategory.sort_order, ForumCategory.id))
    return ok(
        [
            {
                "id": row.id,
                "name": row.name,
                "slug": row.slug,
                "description": row.description,
                "sort_order": row.sort_order,
                "enabled": row.enabled,
            }
            for row in rows
        ]
    )


@router.post("/forum/categories", status_code=201)
def create_forum_category(
    payload: ForumCategoryCreate, db: Session = Depends(get_db)
) -> dict:
    row = ForumCategory(**payload.model_dump())
    db.add(row)
    _commit(db, "论坛分类名称或标识已存在")
    db.refresh(row)
    return ok({"id": row.id, **payload.model_dump()})


@router.patch("/forum/categories/{category_id}")
def update_forum_category(
    category_id: int, payload: ForumCategoryUpdate, db: Session = Depends(get_db)
) -> dict:
    row = db.get(ForumCategory, category_id)
    if not row:
        raise BusinessError("论坛分类不存在", code="forum_category_not_found", status_code=404)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    _commit(db, "论坛分类名称已存在")
    return ok({"id": row.id, "name": row.name, "enabled": row.enabled})


@router.get("/forum/posts")
def forum_posts(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ForumPost)
        .where(ForumPost.deleted_at.is_(None))
        .options(joinedload(ForumPost.author), joinedload(ForumPost.category))
        .order_by(ForumPost.created_at.desc())
        .limit(500)
    )
    return ok(
        [
            {
                "id": row.id,
                "title": row.title,
                "author": row.author.nickname,
                "category": row.category.name,
                "status": row.status,
                "is_pinned": row.is_pinned,
                "is_featured": row.is_featured,
                "created_at": row.created_at,
            }
            for row in rows.unique()
        ]
    )


@router.patch("/forum/posts/{post_id}")
def moderate_post(
    post_id: int, payload: ForumModeration, db: Session = Depends(get_db)
) -> dict:
    row = db.scalar(
        select(ForumPost).where(ForumPost.id == post_id, ForumPost.deleted_at.is_(None))
    )
    if not row:
        raise BusinessError("帖子不存在", code="forum_post_not_found", status_code=404)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    return ok(
        {
            "id": row.id,
            "status": row.status,
            "is_pinned": row.is_pinned,
            "is_featured": row.is_featured,
        }
    )


@router.get("/forum/comments")
def forum_comments(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ForumComment)
        .where(ForumComment.deleted_at.is_(None))
        .options(joinedload(ForumComment.author))
        .order_by(ForumComment.created_at.desc())
        .limit(500)
    )
    return ok(
        [
            {
                "id": row.id,
                "post_id": row.post_id,
                "parent_id": row.parent_id,
                "author": row.author.nickname,
                "content": row.content,
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in rows.unique()
        ]
    )


@router.patch("/forum/comments/{comment_id}")
def moderate_comment(
    comment_id: int, payload: ForumModeration, db: Session = Depends(get_db)
) -> dict:
    row = db.scalar(
        select(ForumComment).where(
            ForumComment.id == comment_id, ForumComment.deleted_at.is_(None)
        )
    )
    if not row:
        raise BusinessError("评论不存在", code="forum_comment_not_found", status_code=404)
    if payload.status is None:
        raise BusinessError("评论审核必须提供状态", code="comment_status_required", status_code=400)
    was_published = row.status == "published"
    will_be_published = payload.status == "published"
    row.status = payload.status
    if was_published != will_be_published:
        post = db.get(ForumPost, row.post_id)
        if post:
            post.comment_count = max(
                0, post.comment_count + (1 if will_be_published else -1)
            )
    db.commit()
    return ok({"id": row.id, "status": row.status})


@router.get("/forum/reports")
def forum_reports(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ForumReport).order_by(ForumReport.created_at.desc()).limit(500))
    return ok(
        [
            {
                "id": row.id,
                "post_id": row.post_id,
                "comment_id": row.comment_id,
                "reporter_id": row.reporter_id,
                "reason": row.reason,
                "status": row.status,
                "handled_by": row.handled_by,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    )


@router.patch("/forum/reports/{report_id}")
def moderate_report(
    report_id: int,
    payload: ForumReportModeration,
    current_user: User = Depends(admin_access),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ForumReport, report_id)
    if not row:
        raise BusinessError("举报不存在", code="forum_report_not_found", status_code=404)
    row.status = payload.status
    row.handled_by = current_user.id
    row.handled_at = datetime.now(timezone.utc)
    db.commit()
    return ok({"id": row.id, "status": row.status})
