from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.helpers import ok
from app.core.client_auth import ClientContext, get_client_context
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.models.client import (
    Cart,
    CartItem,
    ClientAccount,
    ClientAddress,
    ClientNotification,
    Product,
    ProductCategory,
    ProductImage,
    ProductSKU,
    RecycleRequest,
    RetailOrder,
    RetailOrderItem,
)
from app.models.entities import RepairOrder, ServiceTicket, ServiceTicketTimeline
from app.schemas.client import AddressWrite, CartItemAdd, CartItemUpdate, OrderCreate
from app.services.client_uploads import save_client_image
from app.services.client_profiles import client_avatar_url
from app.services.client_auth import identifier_change_status
from app.services.numbering import make_no
from app.storage.local import LocalStorageService


router = APIRouter(prefix="/api/client", tags=["client-shop"])
ZERO = Decimal("0.00")


def _money(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _address_data(row: ClientAddress) -> dict:
    return {
        "id": row.id,
        "recipient_name": row.recipient_name,
        "phone": row.phone,
        "province": row.province,
        "city": row.city,
        "district": row.district,
        "detail": row.detail,
        "postal_code": row.postal_code,
        "is_default": row.is_default,
    }


def _inventory_sellable(row: ProductSKU) -> bool:
    if row.inventory_item_id is None:
        return True
    item = row.inventory_item
    return bool(
        item
        and item.deleted_at is None
        and item.enabled
        and item.client_visible
    )


def _sku_stock_quantity(row: ProductSKU) -> int:
    if row.inventory_item_id is not None and row.inventory_item is not None:
        return int(row.inventory_item.stock_quantity)
    return row.stock_quantity


def _sku_available(row: ProductSKU) -> int:
    return max(0, _sku_stock_quantity(row) - row.reserved_quantity)


def _sku_data(row: ProductSKU) -> dict:
    return {
        "id": row.id,
        "sku": row.sku,
        "name": row.name,
        "attributes": row.attributes_json or {},
        "price": _money(row.price),
        "original_price": _money(row.original_price),
        "stock": _sku_available(row),
        "enabled": row.enabled,
    }


def _product_data(row: Product, *, detail: bool = False) -> dict:
    active_skus = [
        sku
        for sku in row.skus
        if sku.deleted_at is None and sku.enabled and _inventory_sellable(sku)
    ]
    prices = [sku.price for sku in active_skus]
    data = {
        "id": row.id,
        "name": row.name,
        "slug": row.slug,
        "summary": row.summary,
        "status": row.status,
        "featured": row.featured,
        "category": (
            {"id": row.category.id, "name": row.category.name, "slug": row.category.slug}
            if row.category and row.category.deleted_at is None
            else None
        ),
        "price_from": _money(min(prices)) if prices else None,
        "images": [
            {
                "id": image.id,
                "url": f"/api/client/products/images/{image.id}",
                "alt": image.alt_text or row.name,
            }
            for image in sorted(row.images, key=lambda item: (item.sort_order, item.id))
        ],
    }
    if detail:
        data.update(
            {
                "description": row.description,
                "after_sales": row.after_sales,
                "skus": [_sku_data(sku) for sku in active_skus],
            }
        )
    return data


def _cart(db: Session, account_id: int) -> Cart:
    cart = db.scalar(
        select(Cart)
        .where(Cart.account_id == account_id)
        .options(
            selectinload(Cart.items)
            .joinedload(CartItem.sku)
            .joinedload(ProductSKU.product),
            selectinload(Cart.items)
            .joinedload(CartItem.sku)
            .joinedload(ProductSKU.inventory_item),
        )
    )
    if not cart:
        cart = Cart(account_id=account_id)
        db.add(cart)
        db.flush()
    return cart


def _cart_data(cart: Cart) -> dict:
    items = []
    selected_total = ZERO
    for item in cart.items:
        sku = item.sku
        product = sku.product
        available = _sku_available(sku)
        valid = bool(
            sku.deleted_at is None
            and sku.enabled
            and product.deleted_at is None
            and product.status == "published"
            and _inventory_sellable(sku)
        )
        amount = sku.price * item.quantity
        if item.selected and valid:
            selected_total += amount
        items.append(
            {
                "id": item.id,
                "quantity": item.quantity,
                "selected": item.selected,
                "valid": valid,
                "available_stock": available,
                "amount": _money(amount),
                "sku": _sku_data(sku),
                "product": {"id": product.id, "name": product.name, "slug": product.slug},
            }
        )
    return {"id": cart.id, "items": items, "selected_total": _money(selected_total)}


def _order_data(row: RetailOrder) -> dict:
    return {
        "id": row.id,
        "order_no": row.order_no,
        "status": row.status,
        "status_label": {
            "pending_payment": "等待门店确认/收款",
            "paid": "已收款",
            "processing": "备货中",
            "shipped": "已发货",
            "completed": "已完成",
            "cancelled": "已取消",
            "refunding": "退款处理中",
            "refunded": "已退款",
        }.get(row.status, row.status),
        "address": row.address_snapshot_json,
        "delivery_method": row.delivery_method,
        "subtotal": _money(row.subtotal),
        "shipping_fee": _money(row.shipping_fee),
        "discount": _money(row.discount),
        "total_amount": _money(row.total_amount),
        "payment_provider": row.payment_provider,
        "tracking_no": row.tracking_no,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "items": [
            {
                "id": item.id,
                "sku_id": item.sku_id,
                "product_name": item.product_name,
                "sku_name": item.sku_name,
                "sku_code": item.sku_code,
                "attributes": item.attributes_snapshot_json or {},
                "quantity": item.quantity,
                "unit_price": _money(item.unit_price),
                "amount": _money(item.amount),
            }
            for item in row.items
        ],
    }


@router.get("/me")
def profile_home(
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    account_id, customer_id = context.account.id, context.account.customer_id
    counts = {
        "repairs": db.scalar(
            select(func.count(RepairOrder.id)).where(
                RepairOrder.customer_id == customer_id, RepairOrder.deleted_at.is_(None)
            )
        )
        or 0,
        "orders": db.scalar(
            select(func.count(RetailOrder.id)).where(RetailOrder.account_id == account_id)
        )
        or 0,
        "recycles": db.scalar(
            select(func.count(RecycleRequest.id)).where(
                RecycleRequest.account_id == account_id
            )
        )
        or 0,
        "replacements": db.scalar(
            select(func.count(ServiceTicket.id))
            .join(ServiceTicketTimeline, ServiceTicketTimeline.ticket_id == ServiceTicket.id)
            .where(
                ServiceTicket.customer_id == customer_id,
                ServiceTicket.ticket_type == "replacement",
                ServiceTicket.deleted_at.is_(None),
                ServiceTicketTimeline.event_type == "client_replacement_submitted",
            )
        )
        or 0,
        "unread_notifications": db.scalar(
            select(func.count(ClientNotification.id)).where(
                ClientNotification.account_id == account_id,
                ClientNotification.is_read.is_(False),
            )
        )
        or 0,
    }
    counts["work_items"] = (
        counts["repairs"] + counts["orders"] + counts["recycles"] + counts["replacements"]
    )
    return ok(
        {
            "account": {
                "id": context.account.id,
                "username": context.account.username,
                "identifier": f"@{context.account.username}",
                "nickname": context.account.nickname,
                "phone": context.account.phone,
                "email": context.account.email,
                "avatar_url": client_avatar_url(context.account),
            },
            "counts": counts,
            "identifier_change": identifier_change_status(db, account_id),
        }
    )


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    content = await file.read(10 * 1024 * 1024 + 1)
    stored = save_client_image(
        filename=file.filename or "avatar.jpg",
        content_type=file.content_type,
        content=content,
        folder=f"accounts/{context.account.id}/avatar",
    )
    context.account.avatar_path = stored.storage_path
    db.commit()
    db.refresh(context.account)
    return ok({"avatar_url": client_avatar_url(context.account)})


@router.get("/me/avatar")
def avatar(
    context: ClientContext = Depends(get_client_context),
) -> FileResponse:
    if not context.account.avatar_path:
        raise BusinessError("头像不存在", code="avatar_not_found", status_code=404)
    path = LocalStorageService().absolute_path(context.account.avatar_path)
    if not path.is_file():
        raise BusinessError("头像文件已丢失", code="avatar_file_missing", status_code=404)
    return FileResponse(path)


@router.get("/avatars/{account_id}")
def public_avatar(account_id: int, db: Session = Depends(get_db)) -> FileResponse:
    account = db.scalar(
        select(ClientAccount).where(
            ClientAccount.id == account_id, ClientAccount.status == "active"
        )
    )
    if not account or not account.avatar_path:
        raise BusinessError("头像不存在", code="avatar_not_found", status_code=404)
    path = LocalStorageService().absolute_path(account.avatar_path)
    if not path.is_file():
        raise BusinessError("头像文件已丢失", code="avatar_file_missing", status_code=404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/addresses")
def addresses(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    rows = db.scalars(
        select(ClientAddress)
        .where(ClientAddress.account_id == context.account.id)
        .order_by(ClientAddress.is_default.desc(), ClientAddress.id.desc())
    )
    return ok([_address_data(row) for row in rows])


@router.post("/addresses", status_code=201)
def create_address(
    payload: AddressWrite,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    if payload.is_default or not db.scalar(
        select(ClientAddress.id).where(ClientAddress.account_id == context.account.id)
    ):
        db.execute(
            update(ClientAddress)
            .where(ClientAddress.account_id == context.account.id)
            .values(is_default=False)
        )
        values = payload.model_dump() | {"is_default": True}
    else:
        values = payload.model_dump()
    row = ClientAddress(account_id=context.account.id, **values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return ok(_address_data(row))


@router.patch("/addresses/{address_id}")
def update_address(
    address_id: int,
    payload: AddressWrite,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(ClientAddress).where(
            ClientAddress.id == address_id,
            ClientAddress.account_id == context.account.id,
        )
    )
    if not row:
        raise BusinessError("地址不存在", code="address_not_found", status_code=404)
    if payload.is_default:
        db.execute(
            update(ClientAddress)
            .where(ClientAddress.account_id == context.account.id)
            .values(is_default=False)
        )
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    db.commit()
    return ok(_address_data(row))


@router.delete("/addresses/{address_id}")
def delete_address(
    address_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(ClientAddress).where(
            ClientAddress.id == address_id,
            ClientAddress.account_id == context.account.id,
        )
    )
    if not row:
        raise BusinessError("地址不存在", code="address_not_found", status_code=404)
    db.delete(row)
    db.commit()
    return ok({"deleted": True})


@router.get("/product-categories")
def product_categories(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ProductCategory)
        .where(ProductCategory.enabled.is_(True), ProductCategory.deleted_at.is_(None))
        .order_by(ProductCategory.sort_order, ProductCategory.id)
    )
    return ok([{"id": row.id, "name": row.name, "slug": row.slug} for row in rows])


@router.get("/products")
def products(
    q: str = Query(default="", max_length=100),
    category: str | None = Query(default=None, max_length=100),
    featured: bool | None = None,
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(Product)
        .where(Product.status == "published", Product.deleted_at.is_(None))
        .options(
            joinedload(Product.category),
            selectinload(Product.skus).joinedload(ProductSKU.inventory_item),
            selectinload(Product.images),
        )
        .order_by(Product.featured.desc(), Product.created_at.desc())
    )
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.like(term), Product.summary.like(term)))
    if category:
        stmt = stmt.join(ProductCategory).where(ProductCategory.slug == category)
    if featured is not None:
        stmt = stmt.where(Product.featured == featured)
    payload = [_product_data(row) for row in db.scalars(stmt.limit(100)).unique()]
    return ok([row for row in payload if row["price_from"] is not None])


@router.get("/products/{product_id}")
def product_detail(product_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(Product)
        .where(
            Product.id == product_id,
            Product.status == "published",
            Product.deleted_at.is_(None),
        )
        .options(
            joinedload(Product.category),
            selectinload(Product.skus).joinedload(ProductSKU.inventory_item),
            selectinload(Product.images),
        )
    )
    if not row:
        raise BusinessError("商品不存在", code="product_not_found", status_code=404)
    payload = _product_data(row, detail=True)
    if not payload["skus"]:
        raise BusinessError("商品暂不可购买", code="product_not_available", status_code=404)
    return ok(payload)


@router.get("/products/images/{image_id}")
def product_image(image_id: int, db: Session = Depends(get_db)) -> FileResponse:
    image = db.scalar(
        select(ProductImage)
        .join(Product)
        .where(
            ProductImage.id == image_id,
            Product.status == "published",
            Product.deleted_at.is_(None),
        )
    )
    if not image:
        raise BusinessError("商品图片不存在", code="product_image_not_found", status_code=404)
    path = LocalStorageService().absolute_path(image.storage_path)
    if not path.is_file():
        raise BusinessError("商品图片文件已丢失", code="product_image_missing", status_code=404)
    return FileResponse(path)


@router.get("/cart")
def cart(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    return ok(_cart_data(_cart(db, context.account.id)))


@router.post("/cart/items", status_code=201)
def add_cart_item(
    payload: CartItemAdd,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    sku = db.scalar(
        select(ProductSKU)
        .join(Product)
        .where(
            ProductSKU.id == payload.sku_id,
            ProductSKU.enabled.is_(True),
            ProductSKU.deleted_at.is_(None),
            Product.status == "published",
            Product.deleted_at.is_(None),
        )
        .options(joinedload(ProductSKU.inventory_item))
    )
    if not sku or not _inventory_sellable(sku):
        raise BusinessError("商品规格不存在或不可购买", code="sku_not_available", status_code=404)
    cart_row = _cart(db, context.account.id)
    item = db.scalar(
        select(CartItem).where(CartItem.cart_id == cart_row.id, CartItem.sku_id == sku.id)
    )
    next_quantity = payload.quantity + (item.quantity if item else 0)
    if next_quantity > _sku_available(sku):
        raise BusinessError("库存不足", code="insufficient_stock", status_code=409)
    if item:
        item.quantity = next_quantity
        item.selected = True
    else:
        db.add(CartItem(cart_id=cart_row.id, sku_id=sku.id, quantity=payload.quantity))
    db.commit()
    db.expire_all()
    return ok(_cart_data(_cart(db, context.account.id)))


@router.patch("/cart/items/{item_id}")
def update_cart_item(
    item_id: int,
    payload: CartItemUpdate,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(
        select(CartItem)
        .join(Cart)
        .where(CartItem.id == item_id, Cart.account_id == context.account.id)
        .options(joinedload(CartItem.sku).joinedload(ProductSKU.inventory_item))
    )
    if not item:
        raise BusinessError("购物车项目不存在", code="cart_item_not_found", status_code=404)
    if payload.quantity is not None:
        if not _inventory_sellable(item.sku):
            raise BusinessError("商品规格已不可购买", code="sku_not_available", status_code=409)
        if payload.quantity > _sku_available(item.sku):
            raise BusinessError("库存不足", code="insufficient_stock", status_code=409)
        item.quantity = payload.quantity
    if payload.selected is not None:
        item.selected = payload.selected
    db.commit()
    db.expire_all()
    return ok(_cart_data(_cart(db, context.account.id)))


@router.delete("/cart/items/{item_id}")
def delete_cart_item(
    item_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(
        select(CartItem).join(Cart).where(
            CartItem.id == item_id, Cart.account_id == context.account.id
        )
    )
    if not item:
        raise BusinessError("购物车项目不存在", code="cart_item_not_found", status_code=404)
    db.delete(item)
    db.commit()
    db.expire_all()
    return ok(_cart_data(_cart(db, context.account.id)))


@router.post("/orders", status_code=201)
def create_order(
    payload: OrderCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    existing = db.scalar(
        select(RetailOrder)
        .where(RetailOrder.idempotency_key == idempotency_key)
        .options(selectinload(RetailOrder.items))
    )
    if existing:
        if existing.account_id != context.account.id:
            raise BusinessError("重复请求标识冲突", code="idempotency_conflict", status_code=409)
        return ok(_order_data(existing))
    address = db.scalar(
        select(ClientAddress).where(
            ClientAddress.id == payload.address_id,
            ClientAddress.account_id == context.account.id,
        )
    )
    if not address:
        raise BusinessError("收货地址不存在", code="address_not_found", status_code=404)
    cart_items = list(
        db.scalars(
            select(CartItem)
            .join(Cart)
            .where(
                CartItem.id.in_(set(payload.cart_item_ids)),
                Cart.account_id == context.account.id,
            )
            .options(
                joinedload(CartItem.sku).joinedload(ProductSKU.product),
                joinedload(CartItem.sku).joinedload(ProductSKU.inventory_item),
            )
        ).unique()
    )
    if len(cart_items) != len(set(payload.cart_item_ids)):
        raise BusinessError("购物车项目不存在", code="cart_item_not_found", status_code=404)
    subtotal = ZERO
    for item in cart_items:
        sku = item.sku
        if (
            sku.deleted_at is not None
            or not sku.enabled
            or sku.product.deleted_at is not None
            or sku.product.status != "published"
            or not _inventory_sellable(sku)
        ):
            raise BusinessError("购物车中有已下架商品", code="sku_not_available", status_code=409)
        if item.quantity > _sku_available(sku):
            raise BusinessError(f"{sku.name} 库存不足", code="insufficient_stock", status_code=409)
        subtotal += sku.price * item.quantity
    shipping_fee = ZERO
    order = RetailOrder(
        order_no=make_no("SO"),
        account_id=context.account.id,
        customer_id=context.account.customer_id,
        address_snapshot_json=_address_data(address),
        delivery_method=payload.delivery_method,
        status="pending_payment",
        subtotal=subtotal,
        shipping_fee=shipping_fee,
        discount=ZERO,
        total_amount=subtotal + shipping_fee,
        payment_provider="manual",
        idempotency_key=idempotency_key,
    )
    db.add(order)
    db.flush()
    for item in cart_items:
        sku, product = item.sku, item.sku.product
        amount = sku.price * item.quantity
        db.add(
            RetailOrderItem(
                order_id=order.id,
                sku_id=sku.id,
                product_name=product.name,
                sku_name=sku.name,
                sku_code=sku.sku,
                attributes_snapshot_json=sku.attributes_json,
                quantity=item.quantity,
                unit_price=sku.price,
                amount=amount,
            )
        )
        sku.reserved_quantity += item.quantity
        db.delete(item)
    db.add(
        ClientNotification(
            account_id=context.account.id,
            notification_type="order_created",
            title="订单已提交",
            content="门店确认库存和收款后会更新订单状态。",
            resource_type="retail_order",
            resource_id=order.id,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError("订单提交冲突，请刷新后重试", code="order_conflict", status_code=409) from exc
    row = db.scalar(
        select(RetailOrder)
        .where(RetailOrder.id == order.id)
        .options(selectinload(RetailOrder.items))
    )
    return ok(_order_data(row))


@router.get("/orders")
def orders(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    rows = db.scalars(
        select(RetailOrder)
        .where(RetailOrder.account_id == context.account.id)
        .options(selectinload(RetailOrder.items))
        .order_by(RetailOrder.created_at.desc())
    )
    return ok([_order_data(row) for row in rows])


@router.get("/orders/{order_id}")
def order_detail(
    order_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(RetailOrder)
        .where(
            RetailOrder.id == order_id, RetailOrder.account_id == context.account.id
        )
        .options(selectinload(RetailOrder.items))
    )
    if not row:
        raise BusinessError("订单不存在", code="retail_order_not_found", status_code=404)
    return ok(_order_data(row))


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(RetailOrder)
        .where(
            RetailOrder.id == order_id, RetailOrder.account_id == context.account.id
        )
        .options(selectinload(RetailOrder.items))
    )
    if not row:
        raise BusinessError("订单不存在", code="retail_order_not_found", status_code=404)
    if row.status != "pending_payment":
        raise BusinessError("当前订单状态不能取消", code="order_cannot_cancel", status_code=409)
    for item in row.items:
        sku = db.get(ProductSKU, item.sku_id)
        if sku:
            sku.reserved_quantity = max(0, sku.reserved_quantity - item.quantity)
    row.status = "cancelled"
    row.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    return ok(_order_data(row))


@router.get("/notifications")
def notifications(
    unread_only: bool = False,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ClientNotification).where(
        ClientNotification.account_id == context.account.id
    )
    if unread_only:
        stmt = stmt.where(ClientNotification.is_read.is_(False))
    rows = db.scalars(stmt.order_by(ClientNotification.created_at.desc()).limit(200))
    return ok(
        [
            {
                "id": row.id,
                "type": row.notification_type,
                "title": row.title,
                "content": row.content,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "is_read": row.is_read,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    )


@router.post("/notifications/{notification_id}/read")
def read_notification(
    notification_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(ClientNotification).where(
            ClientNotification.id == notification_id,
            ClientNotification.account_id == context.account.id,
        )
    )
    if not row:
        raise BusinessError("消息不存在", code="notification_not_found", status_code=404)
    row.is_read = True
    row.read_at = datetime.now(timezone.utc)
    db.commit()
    return ok({"id": row.id, "is_read": True})
