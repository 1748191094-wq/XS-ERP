# -*- mode: python ; coding: utf-8 -*-
"""
服务管理系统 —— 单文件(onefile) Windows 免 Python 打包配置。

与 packaging/windows_no_python.spec（文件夹版）的区别：
  * 不生成 COLLECT，把 binaries/datas 全部内嵌进单个 ServiceManager.exe；
  * 补齐懒加载/动态导入的第三方依赖：selenium、pyserial、python-multipart。

产物：build/windows-onefile/dist/ServiceManager.exe（单个可执行文件）。

数据安全：本 spec 只打包源码与静态资源（app/static、alembic、member_client.html），
不打包 .db / .env / uploads / backups / output / logs 等任何运营数据。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)


project_root = Path(SPEC).resolve().parents[1]
vendor_dir = project_root / "vendor"

# collect_submodules("app") 需要能 import "app" 包；把项目根目录放进 sys.path，
# 避免从其它工作目录运行打包时因找不到 app 包而静默漏掉全部业务模块。
sys.path.insert(0, str(project_root))
if vendor_dir.is_dir():
    sys.path.insert(0, str(vendor_dir))


def _collect_project_submodules(*packages):
    """遍历项目内包目录，收集所有模块导入名（含各层 __init__ 包名）。

    与 collect_submodules 不同，这里不依赖 import 测试，而是直接扫描源码树。
    这样即使某个子模块在打包环境里 import 失败，也不会被漏掉——而
    uvicorn.run("app.main:app") 这类运行时动态导入的模块（静态分析追踪不到）
    也能被完整收录，避免打包后出现 ModuleNotFoundError。
    """
    result = []
    for package in packages:
        package_dir = project_root / package.replace(".", "/")
        if not package_dir.is_dir():
            continue
        for py in package_dir.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(project_root)
            if rel.name == "__init__.py":
                result.append(".".join(rel.parent.parts))
            else:
                result.append(".".join(rel.with_suffix("").parts))
    return result


def _safe_submodules(*names):
    """收集子模块；未安装的可选依赖安全跳过，不中断打包。"""
    result = []
    for name in names:
        try:
            result.extend(collect_submodules(name))
        except Exception:
            continue
    return result


def _safe_data_files(*names):
    result = []
    for name in names:
        try:
            result.extend(collect_data_files(name))
        except Exception:
            continue
    return result


pypdfium_datas, pypdfium_binaries, pypdfium_hidden = collect_all("pypdfium2")

msvcp140 = project_root / "packaging" / "runtime" / "msvcp140.dll"
extra_binaries = [(str(msvcp140), ".")] if msvcp140.is_file() else []

alembic_datas = [
    (str(path), path.parent.relative_to(project_root).as_posix())
    for path in (project_root / "alembic").rglob("*.py")
    if "__pycache__" not in path.parts
]

datas = [
    (str(project_root / "app" / "static"), "app/static"),
    (str(project_root / "alembic.ini"), "."),
    (str(project_root / "member_client.html"), "."),
]
datas += alembic_datas
datas += pypdfium_datas
datas += _safe_data_files("reportlab", "certifi")
datas += _safe_data_files("tzdata", "openpyxl")

hiddenimports = sorted(set(
    # 业务代码：直接扫描源码树收录全部 app 模块（含 app.main、app.api.*、
    # app.integrations.* 等通过 uvicorn.run("app.main:app") 运行时动态加载的模块）。
    # scripts 运行时模块（run_host/run_sync_node/safe_backup_sqlite）已被静态分析覆盖。
    _collect_project_submodules("app")
    + collect_submodules("reportlab")
    + collect_submodules("uvicorn")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + pypdfium_hidden
    + _safe_submodules(
        "selenium",          # 大疆 SN 查询（Edge 浏览器自动化）
        "serial",            # pyserial：校准/串口设备发现
        "python_multipart",  # FastAPI 附件/表单上传
        "multipart",
        "websocket",         # selenium BiDi 依赖（websocket-client）
        "tzdata",            # Windows 时区数据库
        "openpyxl",          # Excel 导入与模板
    )
))

a = Analysis(
    [str(project_root / "scripts" / "windows_launcher.py")],
    pathex=[str(project_root), str(vendor_dir)],
    binaries=pypdfium_binaries + extra_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 单文件：把 binaries 与 datas 直接交给 EXE，不生成 COLLECT。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ServiceManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
