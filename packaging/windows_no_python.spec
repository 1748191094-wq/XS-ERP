# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules


project_root = Path(SPEC).resolve().parents[1]
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
datas += collect_data_files("reportlab")
datas += collect_data_files("certifi")

hiddenimports = sorted(set(
    collect_submodules("app")
    + collect_submodules("reportlab")
    + collect_submodules("uvicorn")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + pypdfium_hidden
))

a = Analysis(
    [str(project_root / "scripts" / "windows_launcher.py")],
    pathex=[str(project_root)],
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ServiceManager",
)
