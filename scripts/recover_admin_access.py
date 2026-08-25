from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.migrations import assert_database_at_head
from app.services.emergency_recovery import run_interactive_admin_recovery


def main() -> int:
    parser = argparse.ArgumentParser(description="本机紧急恢复管理端管理员账户")
    parser.add_argument("--username", help="要恢复的管理员用户名；省略时交互输入")
    args = parser.parse_args()
    assert_database_at_head()
    return run_interactive_admin_recovery(args.username)


if __name__ == "__main__":
    raise SystemExit(main())
