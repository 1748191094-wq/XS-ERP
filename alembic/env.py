from __future__ import annotations

from datetime import datetime, timezone
from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, inspect, pool

from app.core.config import settings
from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models import client  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _fresh_install_requested(connection) -> bool:
    """Use the current declarative schema only for a completely empty DB going to head.

    Revision 0001 predates the project's migration discipline and calls the live
    ``Base.metadata.create_all``. Replaying later revisions after that dynamic
    schema duplicates their columns and tables. Existing databases must still
    execute the normal revision chain; a blank new install can safely create the
    current schema atomically and then receive the same non-business seed data.
    """

    requested = getattr(getattr(config, "cmd_opts", None), "revision", None)
    return requested in {"head", "heads"} and not inspect(connection).get_table_names()


def _seed_fresh_client_defaults(connection) -> None:
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    connection.execute(
        client.ForumCategory.__table__.insert(),
        [
            {"name": "维修交流", "slug": "repair", "description": "故障与维修经验", "sort_order": 10, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "飞行交流", "slug": "flight", "description": "飞行与航拍交流", "sort_order": 20, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "设备讨论", "slug": "devices", "description": "设备与配件讨论", "sort_order": 30, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "二手交流", "slug": "second-hand", "description": "二手设备经验", "sort_order": 40, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "使用技巧", "slug": "tips", "description": "使用技巧与教程", "sort_order": 50, "enabled": True, "created_at": now, "updated_at": now},
        ],
    )
    connection.execute(
        client.ProductCategory.__table__.insert(),
        [
            {"name": "无人机", "slug": "drones", "sort_order": 10, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "相机与云台", "slug": "cameras-gimbals", "sort_order": 20, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "遥控器", "slug": "controllers", "sort_order": 30, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "电池与配件", "slug": "batteries-accessories", "sort_order": 40, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "二手机", "slug": "preowned", "sort_order": 50, "enabled": True, "created_at": now, "updated_at": now},
            {"name": "其他", "slug": "other", "sort_order": 90, "enabled": True, "created_at": now, "updated_at": now},
        ],
    )
    rules = [
        ("appearance_new", "appearance", "近乎全新", "1.0000", 10),
        ("appearance_excellent", "appearance", "轻微使用痕迹", "0.9200", 20),
        ("appearance_good", "appearance", "正常使用痕迹", "0.8200", 30),
        ("appearance_fair", "appearance", "明显磕碰磨损", "0.6800", 40),
        ("function_normal", "function", "功能正常", "1.0000", 10),
        ("function_partial", "function", "部分功能异常", "0.7000", 20),
        ("function_unusable", "function", "无法正常使用", "0.3500", 30),
        ("gimbal_normal", "gimbal", "云台正常", "1.0000", 10),
        ("gimbal_issue", "gimbal", "云台异常", "0.7500", 20),
        ("lens_normal", "lens", "镜头正常", "1.0000", 10),
        ("lens_scratched", "lens", "镜头划伤", "0.8500", 20),
        ("repair_none", "repair_history", "无拆修", "1.0000", 10),
        ("repair_existing", "repair_history", "有拆修记录", "0.8800", 20),
    ]
    connection.execute(
        client.RecycleRule.__table__.insert(),
        [
            {
                "code": code,
                "rule_group": group,
                "label": label,
                "factor": factor,
                "adjustment": "0.00",
                "enabled": True,
                "sort_order": order,
                "created_at": now,
                "updated_at": now,
            }
            for code, group, label, factor, order in rules
        ],
    )


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        fresh_install = _fresh_install_requested(connection)
        # 新装写入前结束 SQLAlchemy 2 的检查事务。
        if connection.in_transaction():
            connection.rollback()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, render_as_batch=True)
        if fresh_install:
            with connection.begin():
                target_metadata.create_all(bind=connection)
                _seed_fresh_client_defaults(connection)
                head = ScriptDirectory.from_config(config).get_current_head()
                if not head:
                    raise RuntimeError("Alembic head revision is unavailable")
                connection.exec_driver_sql(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO alembic_version (version_num) VALUES (?)", (head,)
                )
        else:
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
