from __future__ import annotations

from enum import StrEnum


class RepairOrderStatus(StrEnum):
    PENDING_INSPECTION = "pending_inspection"
    INSPECTING = "inspecting"
    PENDING_QUOTE = "pending_quote"
    QUOTED = "quoted"
    CUSTOMER_CONFIRMED = "customer_confirmed"
    REPAIRING = "repairing"
    PENDING_TEST = "pending_test"
    PENDING_SHIPPING = "pending_shipping"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class QuoteStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class InventoryTransactionType(StrEnum):
    STOCK_IN = "stock_in"
    STOCK_OUT = "stock_out"
    REPAIR_ISSUE = "repair_issue"
    REPAIR_RETURN = "repair_return"
    ADJUSTMENT = "adjustment"
    DAMAGE = "damage"
    PURCHASE_IN = "purchase_in"
    PURCHASE_RETURN = "purchase_return"
    STOCKTAKE_ADJUSTMENT = "stocktake_adjustment"


class FinanceTransactionType(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    REFUND = "refund"


class ParseStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    PARSED = "parsed"
    UNSUPPORTED = "unsupported"
    FAILED = "parse_failed"
