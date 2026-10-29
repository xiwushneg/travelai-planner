"""管理员重置密码并吊销该用户全部会话（命令行）。

用法：
    python scripts/admin_reset_password.py --username alice --confirm reset-password
    python scripts/admin_reset_password.py --username alice --confirm reset-password --dry-run
    python scripts/admin_reset_password.py --username alice --confirm reset-password --password "自定义密码"

防误操作：必须显式传入 --confirm reset-password；用户名精确匹配（区分大小写），
不支持通配符、前后空格或模糊搜索。随机密码只打印一次，不写入日志或审计明细。
"""
from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit import record_audit  # noqa: E402
from app.auth import PasswordPolicyError, hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.repository import Repository  # noqa: E402

CONFIRM_TOKEN = "reset-password"
ALPHABET = string.ascii_letters + string.digits + "!@#%*-_+"


def generate_password(length: int = 20) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def looks_like_pattern(username: str) -> bool:
    return username != username.strip() or any(ch in username for ch in "%_*?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="管理员重置用户密码（精确用户名 + 显式确认）")
    parser.add_argument("--username", required=True, help="必须与数据库中的用户名完全一致")
    parser.add_argument(
        "--confirm", required=True,
        help=f"显式确认参数，必须为 {CONFIRM_TOKEN}",
    )
    parser.add_argument("--password", help="自定义新密码；缺省时生成随机强密码并打印一次")
    parser.add_argument("--dry-run", action="store_true", help="只检查用户是否存在，不做修改")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    args = parser.parse_args(argv)

    if args.confirm != CONFIRM_TOKEN:
        parser.error(f"确认参数不正确，必须为：{CONFIRM_TOKEN}")
    if looks_like_pattern(args.username):
        parser.error("用户名必须精确匹配：不接受通配符、问号或前后空格。")

    repository = Repository(args.db or settings.db_path)
    user = repository.get_user_exact(args.username)
    if not user:
        print(f"未找到用户名完全匹配的账号：{args.username!r}（不做任何修改）", file=sys.stderr)
        return 3

    if args.dry_run:
        print(f"[dry-run] 将重置 {user['username']!r} 的密码并吊销其全部会话。")
        return 0

    password = args.password or generate_password()
    try:
        password_hash = hash_password(password)
    except PasswordPolicyError as exc:
        print(f"新密码不符合策略：{exc}", file=sys.stderr)
        return 2

    _, revoked = repository.reset_password_and_revoke_sessions(user["username"], password_hash)
    record_audit(
        repository, "password_reset", username=user["username"], ip="cli",
        result="success",
        detail={
            "revoked_sessions": revoked,
            "source": "cli",
            "generated": not bool(args.password),
        },
    )
    print(f"已重置账号 {user['username']!r}，吊销 {revoked} 个会话。")
    if not args.password:
        print(f"新密码（仅显示一次）：{password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
