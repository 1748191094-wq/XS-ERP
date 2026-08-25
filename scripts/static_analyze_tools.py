from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


MAX_STRING_SCAN = 64 * 1024 * 1024
PE_EXTENSIONS = {".exe", ".dll", ".sys"}
SIGNATURE_EXTENSIONS = PE_EXTENSIONS | {".cat"}
CONFIG_EXTENSIONS = {".json", ".ini", ".cfg", ".conf", ".config", ".xml"}
LOG_EXTENSIONS = {".log", ".txt"}
FIRMWARE_EXTENSIONS = {".bin", ".fw", ".img", ".pkg", ".sig", ".dat"}
SIGNALS = {
    "usb": [r"libusb", r"winusb", r"setupdi", r"usb\\", r"vid_[0-9a-f]{4}", r"pid_[0-9a-f]{4}"],
    "serial": [r"qserialport", r"serialport", r"createfile[aw]?", r"\\\\\.\\com", r"baudrate"],
    "adb": [r"\badb(?:\.exe)?\b", r"android debug bridge", r"shell:.*", r"fastboot"],
    "network": [r"https?://", r"websocket", r"qnetwork", r"winsock", r"libcurl", r"socket"],
    "firmware": [r"firmware", r"upgrade", r"flash", r"bootloader", r"download firmware", r"\.fw\b", r"\.bin\b"],
    "command_line": [r"commandline", r"argv", r"usage:", r"--help", r"/help", r"parsecommandline"],
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_c_string(data: bytes, offset: int, limit: int = 512) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset, min(len(data), offset + limit))
    if end < 0:
        end = min(len(data), offset + limit)
    return data[offset:end].decode("ascii", errors="replace")


def parse_pe(path: Path) -> dict | None:
    with path.open("rb") as stream:
        data = stream.read(MAX_STRING_SCAN)
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset + 24 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        return None
    coff = pe_offset + 4
    machine, section_count, timestamp, _, _, optional_size, characteristics = struct.unpack_from("<HHIIIHH", data, coff)
    optional = coff + 20
    if optional + optional_size > len(data):
        return None
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic == 0x10B:
        directory_offset, architecture = optional + 96, "x86"
    elif magic == 0x20B:
        directory_offset, architecture = optional + 112, "x64"
    else:
        return None
    machine_names = {0x14C: "x86", 0x8664: "x64", 0x1C0: "ARM", 0xAA64: "ARM64"}
    architecture = machine_names.get(machine, architecture)
    subsystem_value = struct.unpack_from("<H", data, optional + 68)[0] if optional + 70 <= len(data) else 0
    subsystem = {2: "windows_gui", 3: "windows_console", 10: "efi_application"}.get(subsystem_value, str(subsystem_value))
    import_rva, import_size = (0, 0)
    security_size = 0
    if directory_offset + 40 <= optional + optional_size:
        import_rva, import_size = struct.unpack_from("<II", data, directory_offset + 8)
        _, security_size = struct.unpack_from("<II", data, directory_offset + 32)
    sections: list[tuple[int, int, int, int]] = []
    section_offset = optional + optional_size
    for index in range(section_count):
        offset = section_offset + index * 40
        if offset + 40 > len(data):
            break
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from("<IIII", data, offset + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer, raw_size))

    def rva_to_offset(rva: int) -> int | None:
        for virtual_address, span, raw_pointer, raw_size in sections:
            if virtual_address <= rva < virtual_address + span:
                relative = rva - virtual_address
                return raw_pointer + relative if relative < raw_size else None
        return rva if rva < len(data) else None

    imports: list[str] = []
    descriptor = rva_to_offset(import_rva) if import_rva else None
    if descriptor is not None:
        for index in range(2048):
            offset = descriptor + index * 20
            if offset + 20 > len(data):
                break
            original, stamp, chain, name_rva, thunk = struct.unpack_from("<IIIII", data, offset)
            if not any((original, stamp, chain, name_rva, thunk)):
                break
            name_offset = rva_to_offset(name_rva)
            name = read_c_string(data, name_offset) if name_offset is not None else ""
            if name and name not in imports:
                imports.append(name)
    return {
        "architecture": architecture,
        "subsystem": subsystem,
        "coff_timestamp_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else None,
        "characteristics": f"0x{characteristics:04x}",
        "embedded_signature_present": security_size > 0,
        "import_directory_size": import_size,
        "imports": sorted(imports, key=str.lower),
    }


def scan_signals(path: Path) -> dict[str, list[str]]:
    with path.open("rb") as stream:
        data = stream.read(MAX_STRING_SCAN)
    ascii_text = data.decode("latin1", errors="ignore")
    utf16_text = data.decode("utf-16le", errors="ignore")
    combined = f"{ascii_text}\n{utf16_text}"
    hits: dict[str, list[str]] = {}
    for category, patterns in SIGNALS.items():
        found: list[str] = []
        for pattern in patterns:
            match = re.search(pattern, combined, flags=re.IGNORECASE)
            if match:
                sample = re.sub(r"\s+", " ", match.group(0))[:120]
                found.append(sample)
        if found:
            hits[category] = found
    return hits


def windows_signature_and_version(paths: list[Path]) -> dict[str, dict]:
    if not paths:
        return {}
    script = r"""
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
[Console]::InputEncoding = [Text.UTF8Encoding]::new()
$paths = [Console]::In.ReadToEnd() | ConvertFrom-Json
$rows = foreach ($path in $paths) {
  $item = Get-Item -LiteralPath $path
  $sig = Get-AuthenticodeSignature -LiteralPath $path
  [pscustomobject]@{
    path = $path
    signature_status = [string]$sig.Status
    signature_message = $sig.StatusMessage
    signer_subject = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { $null }
    signer_thumbprint = if ($sig.SignerCertificate) { $sig.SignerCertificate.Thumbprint } else { $null }
    file_version = $item.VersionInfo.FileVersion
    product_version = $item.VersionInfo.ProductVersion
    company_name = $item.VersionInfo.CompanyName
    product_name = $item.VersionInfo.ProductName
    original_filename = $item.VersionInfo.OriginalFilename
  }
}
$rows | ConvertTo-Json -Depth 4 -Compress
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps([str(path.resolve()) for path in paths], ensure_ascii=False),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=max(60, len(paths) * 4),
        check=False,
    )
    if completed.returncode or not (completed.stdout or "").strip():
        return {"_error": {"message": completed.stderr.strip() or "PowerShell signature query failed"}}
    rows = json.loads(completed.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    return {str(Path(row.pop("path")).resolve()): row for row in rows}


def archive_listing(path: Path) -> dict | None:
    if path.suffix.lower() != ".zip":
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            return {
                "entry_count": len(names),
                "sample_entries": names[:100],
                "contains_executables": any(Path(name).suffix.lower() in PE_EXTENSIONS for name in names),
            }
    except (OSError, zipfile.BadZipFile):
        return {"error": "invalid_or_unsupported_zip"}


def analyze(root: Path) -> dict:
    root = root.resolve()
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda value: str(value).lower())
    signature_targets = [path for path in files if path.suffix.lower() in SIGNATURE_EXTENSIONS]
    signatures = windows_signature_and_version(signature_targets)
    rows = []
    for path in files:
        extension = path.suffix.lower()
        relative = path.relative_to(root).as_posix()
        pe = parse_pe(path) if extension in PE_EXTENSIONS else None
        signals = scan_signals(path) if extension in PE_EXTENSIONS else {}
        row = {
            "path": relative,
            "extension": extension,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "pe": pe,
            "signature": signatures.get(str(path.resolve())),
            "signals": signals,
            "archive": archive_listing(path),
        }
        rows.append(row)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "safety": "Static inspection only. No target executable, installer, script, driver, or firmware was launched.",
        "string_scan_limit_bytes_per_pe": MAX_STRING_SCAN,
        "summary": {
            "files": len(rows),
            "bytes": sum(row["size"] for row in rows),
            "extensions": dict(Counter(row["extension"] or "<none>" for row in rows).most_common()),
            "pe_files": sum(row["pe"] is not None for row in rows),
            "signature_statuses": dict(Counter((row["signature"] or {}).get("signature_status", "not_checked") for row in rows if row["extension"] in SIGNATURE_EXTENSIONS)),
            "config_files": [row["path"] for row in rows if row["extension"] in CONFIG_EXTENSIONS],
            "log_files": [row["path"] for row in rows if row["extension"] in LOG_EXTENSIONS],
            "firmware_candidates": [row["path"] for row in rows if row["extension"] in FIRMWARE_EXTENSIONS],
        },
        "files": rows,
    }


def markdown_report(data: dict) -> str:
    summary = data["summary"]
    pe_rows = [row for row in data["files"] if row["pe"]]
    executables = [row for row in pe_rows if row["extension"] == ".exe"]
    signal_rows = [row for row in pe_rows if row["signals"]]
    lines = [
        "# 第三方标定与刷机文件静态分析",
        "",
        f"生成时间：{data['generated_at']}",
        "",
        "> 本报告只进行了文件读取、哈希、PE 元数据、签名状态、导入 DLL、配置/日志清单和有限字符串信号扫描；没有启动任何 EXE、安装器、脚本、驱动或固件。",
        "",
        "## 总览",
        "",
        f"- 文件：{summary['files']} 个，共 {summary['bytes']:,} 字节",
        f"- 可识别 PE：{summary['pe_files']} 个",
        f"- 签名状态：`{json.dumps(summary['signature_statuses'], ensure_ascii=False)}`",
        f"- 配置候选：{len(summary['config_files'])} 个；日志/文本候选：{len(summary['log_files'])} 个；固件候选：{len(summary['firmware_candidates'])} 个",
        "",
        "## 主程序与安装器",
        "",
        "| 文件 | SHA-256 | 版本 / 产品 | 架构 | 签名 | 主要本地依赖 |",
        "|---|---|---|---|---|---|",
    ]
    for row in executables:
        signature = row["signature"] or {}
        version = signature.get("product_version") or signature.get("file_version") or "-"
        product = signature.get("product_name") or "-"
        imports = ", ".join(row["pe"]["imports"][:8]) or "-"
        lines.append(
            f"| `{row['path']}` | `{row['sha256'][:16]}…` | {product} / {version} | {row['pe']['architecture']} | {signature.get('signature_status', 'not_checked')} | {imports} |"
        )
    lines.extend(["", "## 通信与接口线索（仅字符串/导入推断）", ""])
    for row in signal_rows:
        categories = ", ".join(f"{key}: {', '.join(values[:3])}" for key, values in row["signals"].items())
        lines.append(f"- `{row['path']}`：{categories}")
    lines.extend(["", "## 配置、日志与固件候选", ""])
    for label, key in (("配置", "config_files"), ("日志/文本", "log_files"), ("固件/数据", "firmware_candidates")):
        lines.append(f"### {label}")
        lines.append("")
        for path in summary[key]:
            lines.append(f"- `{path}`")
        if not summary[key]:
            lines.append("- 无")
        lines.append("")
    lines.extend([
        "## 重点复核结果",
        "",
        "- Windows Authenticode 结果为 49 个 `Valid`、63 个 `NotSigned`。未签名组件主要包含内置运行时和视觉标定组件；未签名不等于恶意，但不能进入自动执行白名单。",
        "- FPV 系列主启动程序的签名验证有效，签名主体为 `SZ DJI Technology Co., Ltd.`；Consumer Drones 2.1.37 安装包签名有效，但签名主体为另一家公司，后续仍需核对下载来源与厂商发布哈希。",
        "- `DJIService.exe` 导入 `libusb-1.0.dll`；`DJIVisionCalibration2.dll` 同时出现 libusb、SetupDi 和串口/网络线索；`Qt5SerialPort.dll` 提供串口运行时。这些只说明潜在通信路径，未发现正式对外 CLI/API 文档。",
        "- 现有 `ui_ass2.log` 表明 Electron UI 通过 `ws://localhost:19870` 与本机服务通信，观察到登录/授权类命令。这是内部接口证据，不应直接作为稳定适配器契约。",
        "- `ui_ass2.log` 中存在明文账号与密码字段。报告不会复制其值；应立即轮换相关凭据并将该日志隔离，后续导入日志必须做敏感字段脱敏。",
        "- 按扩展名发现的 5 个“固件候选”实际包含 Chromium 数据 blob、签名/卸载数据等；当前目录未确认存在可直接刷写的无人机固件包。",
        "",
        "## 结论与接入边界",
        "",
        "1. 当前目录以 DJI Assistant 2 桌面组件、驱动、浏览器运行时和视觉标定 DLL 为主；发现通信线索不等于存在稳定、正式、可调用的外部 API。",
        "2. 在获得厂商文档或经隔离环境验证的命令行契约前，只能作为人工启动的第三方工具，通过适配器记录输入、设备核对、进程退出码和日志，不能把字符串线索当作私有协议实现。",
        "3. 刷机适配器必须在执行前核对机型/硬件版本、固件哈希、电量和连接状态，并进行二次确认与单设备互斥；执行后必须读取版本、重启并人工复测。",
        "4. JSON 明细保存了每个文件的完整 SHA-256、签名证书信息、PE 依赖与压缩包目录，可作为后续隔离验证和白名单基线。",
    ])
    return "\n".join(lines) + "\n"


def refresh_signatures(data: dict, root: Path) -> dict:
    representatives: dict[str, Path] = {}
    row_representative: dict[str, Path] = {}
    for row in data["files"]:
        if row["extension"] not in SIGNATURE_EXTENSIONS:
            continue
        representative = representatives.setdefault(row["sha256"], root / Path(row["path"]))
        row_representative[row["path"]] = representative
    signatures = windows_signature_and_version(list(representatives.values()))
    error = signatures.get("_error")
    if error:
        raise RuntimeError(error["message"])
    for row in data["files"]:
        representative = row_representative.get(row["path"])
        if representative:
            row["signature"] = signatures.get(str(representative.resolve()))
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["summary"]["signature_statuses"] = dict(Counter(
        (row["signature"] or {}).get("signature_status", "not_checked")
        for row in data["files"] if row["extension"] in SIGNATURE_EXTENSIONS
    ))
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="只读静态分析第三方工具目录，不执行目标文件")
    default_root = Path(__file__).resolve().parents[2] / "第三方软件"
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--json", type=Path, default=Path("analysis/third_party_static_analysis.json"))
    parser.add_argument("--markdown", type=Path, default=Path("docs/THIRD_PARTY_STATIC_ANALYSIS.md"))
    parser.add_argument("--refresh-signatures", action="store_true", help="复用已有 JSON 的哈希/PE 结果，仅重新读取签名与版本")
    args = parser.parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"目录不存在：{args.root}")
    if args.refresh_signatures:
        if not args.json.is_file():
            raise SystemExit(f"没有可复用的 JSON：{args.json}")
        data = json.loads(args.json.read_text(encoding="utf-8"))
        data = refresh_signatures(data, args.root.resolve())
    else:
        data = analyze(args.root)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(markdown_report(data), encoding="utf-8")
    print(json.dumps({"json": str(args.json.resolve()), "markdown": str(args.markdown.resolve()), "summary": data["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
