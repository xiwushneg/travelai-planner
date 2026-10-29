"""审计哈希链增量迁移（不丢数据，可重复执行）。

用法：
    python scripts/migrate_audit_chain.py              # 打印将执行的 DDL
    python scripts/migrate_audit_chain.py --apply      # 备份后补加 prev_hash / entry_hash
    python scripts/migrate_audit_chain.py --rollback   # 备份后删除两列（SQLite >= 3.35）

迁移前已存在的记录保持 NULL（校验时只统计为 legacy），新写入记录自动串链。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

COLUMNS = ("prev_hash", "entry_hash")


def backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db_path.stem}-chain-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    with sqlite3.connect(db_path) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    return target


def existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(audit_events)")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计哈希链迁移")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    parser.add_argument("--apply", action="store_true", help="执行迁移")
    parser.add_argument("--rollback", action="store_true", help="回滚：删除哈希列")
    args = parser.parse_args(argv)

    db_path = Path(args.db or settings.db_path)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    try:
        if "audit_events" not in {
            name for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }:
            print("audit_events 表不存在，请先运行 migrate_audit.py --apply", file=sys.stderr)
            return 1
        columns = existing_columns(conn)
        todo = [col for col in COLUMNS if col not in columns]
        if args.apply:
            if not todo:
                print("无需迁移：哈希列已存在")
                return 0
            target = backup(db_path)
            print(f"备份：{target}")
            for col in todo:
                conn.execute(f"ALTER TABLE audit_events ADD COLUMN {col} TEXT")
            conn.commit()
            print(f"已补列：{', '.join(todo)}；历史记录保持 NULL（legacy）")
            return 0
        if args.rollback:
            present = [col for col in COLUMNS if col in columns]
            if not present:
                print("无需回滚：哈希列不存在")
                return 0
            target = backup(db_path)
            print(f"备份：{target}")
            try:
                for col in present:
                    conn.execute(f"ALTER TABLE audit_events DROP COLUMN {col}")
                conn.commit()
            except sqlite3.OperationalError as exc:
                print(f"当前 SQLite 不支持 DROP COLUMN：{exc}", file=sys.stderr)
                return 1
            print(f"已回滚：删除列 {', '.join(present)}")
            return 0
        for col in todo:
            print(f"ALTER TABLE audit_events ADD COLUMN {col} TEXT")
        if not todo:
            print("-- 无需迁移：哈希列已存在")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
