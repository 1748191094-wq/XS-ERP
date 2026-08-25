from __future__ import annotations

import getpass
import sys

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import validate_emergency_password
from app.models.entities import User
from app.services.admin_credentials import recover_admin_account
from app.services.backup import BackupService


def run_interactive_admin_recovery(username: str | None = None) -> int:
    if not sys.stdin.isatty():
        print("紧急恢复必须在本机交互式终端中运行。", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        admins = list(
            db.scalars(select(User).where(User.role == "admin").order_by(User.username))
        )
        if not admins:
            print("数据库中没有管理端管理员，无法执行恢复。", file=sys.stderr)
            return 2
        print("\n可恢复的管理端管理员：")
        for admin in admins:
            print(f"  {admin.username}（{admin.display_name}，{'启用' if admin.enabled else '停用'}）")
        selected = (username or input("\n请输入要恢复的管理员用户名：")).strip()
        confirmation = input(f"输入“恢复 {selected}”确认本机紧急恢复：").strip()
        if confirmation != f"恢复 {selected}":
            print("确认文字不匹配，未修改任何账户。", file=sys.stderr)
            return 2
        password = getpass.getpass("请输入新的紧急恢复密码（至少 16 位，含字母和数字）：")
        repeated = getpass.getpass("请再次输入新密码：")
        if password != repeated:
            print("两次密码不一致，未修改任何账户。", file=sys.stderr)
            return 2
        try:
            validate_emergency_password(password)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        backup = BackupService.create(
            db,
            created_by=None,
            notes=f"管理员 {selected} 本机紧急恢复前自动备份",
        )
        user, revoked = recover_admin_account(
            db, username=selected, new_password=password
        )
        print(f"\n恢复完成：{user.username} 已启用，撤销会话 {revoked} 个。")
        print(f"恢复前备份：{backup.storage_path}")
        print(f"SHA-256：{backup.sha256}")
        print("请立即登录验证，并妥善保管新密码。")
    return 0
