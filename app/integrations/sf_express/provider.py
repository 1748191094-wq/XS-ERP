from __future__ import annotations

from typing import Protocol


class ShippingProvider(Protocol):
    def create_order(self, shipment_id: int) -> dict: ...
    def cancel_order(self, shipment_id: int) -> dict: ...
    def query_tracking(self, tracking_no: str) -> dict: ...
    def download_label(self, shipment_id: int) -> bytes: ...


class MockSFExpressProvider:
    def create_order(self, shipment_id: int) -> dict:
        return {"provider": "mock_sf", "shipment_id": shipment_id, "created": False, "message": "未配置顺丰真实账号"}

    def cancel_order(self, shipment_id: int) -> dict:
        return {"provider": "mock_sf", "shipment_id": shipment_id, "cancelled": False}

    def query_tracking(self, tracking_no: str) -> dict:
        return {"provider": "mock_sf", "tracking_no": tracking_no, "events": [], "simulated": True}

    def download_label(self, shipment_id: int) -> bytes:
        return b""


def get_shipping_provider() -> ShippingProvider:
    return MockSFExpressProvider()
