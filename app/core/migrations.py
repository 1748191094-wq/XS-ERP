from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.core.config import BASE_DIR, RESOURCE_DIR, settings


def alembic_config() -> Config:
    ini_path = RESOURCE_DIR / "alembic.ini"
    script_path = RESOURCE_DIR / "alembic"
    if not ini_path.is_file() or not script_path.is_dir():
        raise RuntimeError("程序包缺少数据库迁移资源，请重新安装完整程序包")
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(script_path))
    config.set_main_option("prepend_sys_path", str(RESOURCE_DIR))
    return config


def expected_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if not head:
        raise RuntimeError("无法确定数据库迁移目标版本")
    return str(head)


def sqlite_database_path() -> Path | None:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    return path.resolve() if path.is_absolute() else (BASE_DIR / path).resolve()


def current_revision() -> str | None:
    database = sqlite_database_path()
    if database is None:
        return expected_revision()
    if not database.is_file() or database.stat().st_size == 0:
        return None
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if not table:
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row else None


def assert_database_at_head() -> str:
    expected = expected_revision()
    current = current_revision()
    if current != expected:
        raise RuntimeError(
            "数据库版本不符合程序要求："
            f"当前 {current or '未初始化'}，需要 {expected}。"
            "请使用启动 CMD 或运行 scripts/windows_launcher.py migrate；"
            "程序不会静默修改数据库。"
        )
    return expected
