"""审计与会话索引的增量迁移（可重复执行、不丢数据）。

用法：
    python scripts/migrate_audit.py              # 打印将要执行的 DDL，不做修改
    python scripts/migrate_audit.py --apply      # 备份后执行迁移
    python scripts/migrate_audit.py --rollback   # 备份后删除本迁移新增的对象

迁移只做 ADD（建表/建索引），不修改 users/trips/sessions 等已有表的列与数据。
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

DDL = [
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    """CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        actor_key TEXT,
        event_type TEXT NOT NULL,
        ip TEXT,
        result TEXT NOT NULL,
        detail TEXT,
        prev_hash TEXT,
        entry_hash TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS register_attempts (
        key TEXT PRIMARY KEY,
        window_start TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_key, created_at)",
]

ROLLBACK = [
    "DROP INDEX IF EXISTS idx_audit_actor",
    "DROP INDEX IF EXISTS idx_audit_created",
    "DROP TABLE IF EXISTS audit_events",
    "DROP TABLE IF EXISTS register_attempts",
    "DROP INDEX IF EXISTS idx_sessions_user",
]


def backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db_path.stem}-audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(db_path, target)
    return target


def stats(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        result = {"tables": sorted(tables)}
        if "audit_events" in tables:
            result["audit_rows"] = conn.execute(
                "SELECT count(*) FROM audit_events"
            ).fetchone()[0]
        if "sessions" in tables:
            result["session_rows"] = conn.execute(
                "SELECT count(*) FROM sessions"
            ).fetchone()[0]
        return result
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计表与会话索引迁移")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    parser.add_argument("--apply", action="store_true", help="执行迁移（默认只打印）")
    parser.add_argument("--rollback", action="store_true", help="回滚本迁移新增对象")
    args = parser.parse_args(argv)

    if args.apply and args.rollback:
        parser.error("--apply 与 --rollback 不能同时使用")

    db_path = Path(args.db or settings.db_path)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1

    statements = ROLLBACK if args.rollback else DDL
    if not args.apply and not args.rollback:
        print("将执行以下 DDL（加 --apply 生效，加 --rollback 回滚）：")
        for statement in statements:
            print(f"  {statement.strip()};")
        print(f"当前统计：{stats(db_path)}")
        return 0

    backup_path = backup(db_path)
    print(f"已备份数据库：{backup_path}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"{'回滚' if args.rollback else '迁移'}完成。当前统计：{stats(db_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
