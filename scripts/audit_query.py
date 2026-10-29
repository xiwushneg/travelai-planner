"""只读审计查询（命令行）。

用法：
    python scripts/audit_query.py --limit 20
    python scripts/audit_query.py --event login_failed --limit 50
    python scripts/audit_query.py --username alice --json

数据库以只读 URI 打开，不会创建表或写入任何数据。
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

from app.audit import hash_identifier  # noqa: E402
from app.config import settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查询审计事件（只读）")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    parser.add_argument("--limit", type=int, default=20, help="返回条数，默认 20，最大 200")
    parser.add_argument("--event", help="按事件类型过滤，如 login_failed")
    parser.add_argument("--username", help="按用户名过滤（内部会哈希后匹配）")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于程序处理")
    args = parser.parse_args(argv)

    db_path = Path(args.db or settings.db_path)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1

    clauses, params = [], []
    if args.event:
        clauses.append("event_type=?")
        params.append(args.event)
    if args.username:
        clauses.append("actor_key=?")
        params.append(hash_identifier(args.username.lower()))
    sql = "SELECT created_at,event_type,result,ip,actor_key,detail FROM audit_events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(args.limit, 200)))

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
    except sqlite3.OperationalError as exc:
        print(f"查询失败（审计表可能尚未创建）：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.json:
        for row in rows:
            row["detail"] = json.loads(row["detail"]) if row["detail"] else None
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("没有匹配的审计记录。")
        return 0
    print(f"{'时间':<34} {'事件':<22} {'结果':<10} {'IP':<16} 标识")
    for row in rows:
        actor = (row["actor_key"] or "-")[:12]
        print(
            f"{row['created_at']:<34} {row['event_type']:<22} {row['result']:<10} "
            f"{(row['ip'] or '-'):<16} {actor} {row['detail'] or ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
