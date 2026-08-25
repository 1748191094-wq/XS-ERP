from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


DJI_USB_VENDOR_ID = "2CA3"
_PNP_ID_PATTERN = re.compile(
    r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:&MI_([0-9A-F]{2}))?\\([^\r\n]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class KnownUsbProduct:
    product_name: str
    profile_key: str
    evidence: str


# 仅记录已验证映射，不推测新型号兼容性。
KNOWN_USB_PRODUCTS: dict[tuple[str, str], KnownUsbProduct] = {
    (DJI_USB_VENDOR_ID, "0020"): KnownUsbProduct(
        product_name="DJI Avata 2",
        profile_key="dji_avata_2_local_baseline",
        evidence="user_confirmed_normal_aircraft_and_windows_usb_enumeration_2026_07_22",
    ),
}


def _run_pnputil_connected() -> set[str]:
    """Return cached Windows PnP IDs without opening a hardware handle."""
    if sys.platform != "win32":
        return set()
    completed = subprocess.run(
        ["pnputil", "/enum-devices", "/connected", "/ids"],
        capture_output=True,
        check=False,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # PnP identifiers are ASCII even when the surrounding localized output is not.
    text = (completed.stdout + completed.stderr).decode("ascii", errors="ignore")
    return {match.group(0).strip().upper() for match in _PNP_ID_PATTERN.finditer(text)}


def classify_interface(*, interface_number: str | None, device_class: str, service: str) -> str:
    normalized_class = device_class.casefold()
    normalized_service = service.casefold()
    if normalized_class == "net" or "rndis" in normalized_service:
        return "rndis_network"
    if normalized_service == "usbstor":
        return "mass_storage"
    if "libusb" in normalized_class or normalized_service.startswith("libusb"):
        return "vendor_bulk_unopened"
    if interface_number is None:
        return "composite_parent"
    return "unknown_unopened"


def _registry_value(key: Any, name: str) -> str:
    try:
        import winreg

        value, _kind = winreg.QueryValueEx(key, name)
    except (FileNotFoundError, OSError):
        return ""
    if isinstance(value, (list, tuple)):
        return ";".join(str(item) for item in value)
    return str(value)


def _read_windows_registry_interfaces(present_ids: set[str]) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    import winreg

    interfaces: list[dict[str, Any]] = []
    base_path = r"SYSTEM\CurrentControlSet\Enum\USB"
    try:
        base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path, 0, winreg.KEY_READ)
    except OSError:
        return interfaces
    with base:
        hardware_index = 0
        while True:
            try:
                hardware_key_name = winreg.EnumKey(base, hardware_index)
            except OSError:
                break
            hardware_index += 1
            match = re.fullmatch(
                r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:&MI_([0-9A-F]{2}))?",
                hardware_key_name,
                re.IGNORECASE,
            )
            if not match or match.group(1).upper() != DJI_USB_VENDOR_ID:
                continue
            try:
                hardware_key = winreg.OpenKey(base, hardware_key_name, 0, winreg.KEY_READ)
            except OSError:
                continue
            with hardware_key:
                instance_index = 0
                while True:
                    try:
                        instance_name = winreg.EnumKey(hardware_key, instance_index)
                    except OSError:
                        break
                    instance_index += 1
                    pnp_id = f"USB\\{hardware_key_name}\\{instance_name}".upper()
                    if pnp_id not in present_ids:
                        continue
                    try:
                        instance_key = winreg.OpenKey(
                            hardware_key, instance_name, 0, winreg.KEY_READ
                        )
                    except OSError:
                        continue
                    with instance_key:
                        device_class = _registry_value(instance_key, "Class")
                        service = _registry_value(instance_key, "Service")
                        interface_number = match.group(3)
                        interfaces.append(
                            {
                                "vendor_id": match.group(1).upper(),
                                "product_id": match.group(2).upper(),
                                "interface_number": interface_number,
                                "device_class": device_class,
                                "service": service,
                                "role": classify_interface(
                                    interface_number=interface_number,
                                    device_class=device_class,
                                    service=service,
                                ),
                            }
                        )
    return sorted(
        interfaces,
        key=lambda item: (
            item["interface_number"] is not None,
            item["interface_number"] or "",
        ),
    )


def discover_connected_dji_devices() -> dict[str, Any]:
    """Discover DJI USB nodes using OS caches only.

    This function does not open USB, serial, storage, or network handles and does
    not transmit a command to the connected aircraft.
    """
    safety = {
        "discovery_only": True,
        "hardware_handles_opened": False,
        "device_commands_sent": False,
        "usb_control_transfers_sent": False,
        "network_probes_sent": False,
        "storage_content_read": False,
        "write_operations_available": False,
    }
    if sys.platform != "win32":
        return {
            "available": False,
            "reason": "windows_pnp_required",
            "devices": [],
            "safety": safety,
        }
    try:
        present_ids = _run_pnputil_connected()
    except (OSError, subprocess.SubprocessError):
        return {
            "available": False,
            "reason": "windows_pnp_enumeration_failed",
            "devices": [],
            "safety": safety,
        }

    parent_products: set[tuple[str, str]] = set()
    for pnp_id in present_ids:
        match = _PNP_ID_PATTERN.search(pnp_id)
        if (
            match
            and match.group(3) is None
            and match.group(1).upper() == DJI_USB_VENDOR_ID
        ):
            parent_products.add((match.group(1).upper(), match.group(2).upper()))

    registry_interfaces = _read_windows_registry_interfaces(present_ids)
    devices = []
    for vendor_id, product_id in sorted(parent_products):
        known = KNOWN_USB_PRODUCTS.get((vendor_id, product_id))
        matching_interfaces = [
            item
            for item in registry_interfaces
            if item["interface_number"] is not None
            and item["vendor_id"] == vendor_id
            and item["product_id"] == product_id
        ]
        devices.append(
            {
                "vendor_id": vendor_id,
                "product_id": product_id,
                "product_name": known.product_name if known else "DJI USB device",
                "profile_key": known.profile_key if known else None,
                "mapping_evidence": known.evidence if known else "usb_vendor_only",
                "serial_number": None,
                "serial_number_reliable": False,
                "interfaces": matching_interfaces,
                "calibration_profile_available": False,
                "live_execution_available": False,
            }
        )
    return {
        "available": True,
        "reason": None,
        "discovery_mode": "windows_pnp_and_registry_cache_only",
        "devices": devices,
        "safety": safety,
    }
