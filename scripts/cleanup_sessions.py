"""独立清理过期会话（幂等，可重复执行，无调度框架）。

用法：
    python scripts/cleanup_sessions.py                 # 删除过期会话并报告数量
    python scripts/cleanup_sessions.py --dry-run       # 只统计不删除
    python scripts/cleanup_sessions.py --json          # JSON 输出，便于日志采集

退出码：0 成功；1 数据库不存在。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.repository import Repository, utcnow  # noqa: E402


def count_expired(repository: Repository) -> int:
    with repository.connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM sessions WHERE expires_at<=?", (utcnow(),)
        ).fetchone()
    return int(row[0])


def count_sessions(repository: Repository) -> int:
    with repository.connect() as conn:
        row = conn.execute("SELECT count(*) FROM sessions").fetchone()
    return int(row[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理过期会话（幂等）")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    parser.add_argument("--dry-run", action="store_true", help="只统计过期数量，不做删除")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    db_path = Path(args.db or settings.db_path)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1

    repository = Repository(db_path)
    if args.dry_run:
        result = {
            "dry_run": True,
            "would_delete": count_expired(repository),
            "stale_login_failures": repository.count_stale_login_failures(),
            "remaining": count_sessions(repository),
        }
    else:
        deleted = repository.delete_expired_sessions()
        deleted_login_failures = repository.delete_stale_login_failures()
        result = {
            "deleted": deleted,
            "deleted_login_failures": deleted_login_failures,
            "remaining": count_sessions(repository),
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result.get("dry_run"):
        print(
            f"[dry-run] 将清理过期会话 {result['would_delete']} 个、"
            f"失效登录计数 {result['stale_login_failures']} 条，"
            f"当前共有 {result['remaining']} 个会话。"
        )
    else:
        print(
            f"已清理过期会话 {result['deleted']} 个、"
            f"失效登录计数 {result['deleted_login_failures']} 条，"
            f"当前剩余 {result['remaining']} 个会话。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
