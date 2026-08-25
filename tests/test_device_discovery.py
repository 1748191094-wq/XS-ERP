from __future__ import annotations

from app.integrations.calibration.device_discovery import (
    DJI_USB_VENDOR_ID,
    _PNP_ID_PATTERN,
    classify_interface,
)


def test_readonly_dji_interface_classification():
    assert classify_interface(
        interface_number="00", device_class="Net", service="RNDISMP"
    ) == "rndis_network"
    assert classify_interface(
        interface_number="02", device_class="USB", service="USBSTOR"
    ) == "mass_storage"
    assert classify_interface(
        interface_number="03",
        device_class="libusb-win32 devices",
        service="libusb0_device",
    ) == "vendor_bulk_unopened"
    assert classify_interface(
        interface_number="07", device_class="", service=""
    ) == "unknown_unopened"


def test_pnp_pattern_keeps_vendor_product_and_interface_separate():
    parent = _PNP_ID_PATTERN.search(
        r"USB\VID_2CA3&PID_0020\123456789ABCDEF"
    )
    child = _PNP_ID_PATTERN.search(
        r"USB\VID_2CA3&PID_0020&MI_03\6&3958c552&1&0003"
    )
    assert parent is not None and child is not None
    assert parent.group(1).upper() == DJI_USB_VENDOR_ID
    assert parent.group(2).upper() == "0020"
    assert parent.group(3) is None
    assert child.group(3).upper() == "03"
