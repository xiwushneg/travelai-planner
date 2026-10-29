"""校验审计哈希链（只读）。

用法：
    python scripts/verify_audit_chain.py
    python scripts/verify_audit_chain.py --json

数据库以只读 URI 打开；断链时退出码为 1，并打印首个破损事件 id。
旧记录（迁移前写入、无 entry_hash）只统计不校验。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit import verify_audit_chain  # noqa: E402
from app.config import settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验审计哈希链（只读）")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    db_path = Path(args.db or settings.db_path)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT id,created_at,actor_key,event_type,ip,result,detail,prev_hash,entry_hash "
                "FROM audit_events ORDER BY created_at ASC, rowid ASC"
            ).fetchall()
        ]
    except sqlite3.OperationalError as exc:
        print(f"查询失败（审计表或哈希列不存在，请先运行 migrate_audit_chain.py）：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    report = verify_audit_chain(rows)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        status = "OK" if report["ok"] else f"BROKEN at {report['first_broken']}"
        print(
            f"{status}  rows={report['rows']} hashed={report['hashed']} legacy={report['legacy']}"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
