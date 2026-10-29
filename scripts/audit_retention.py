"""审计留存清理：删除超过保留期的旧事件（幂等，可重复执行）。

用法：
    python scripts/audit_retention.py --days 180 --dry-run --json
    python scripts/audit_retention.py --days 180

只删除最旧的一段记录；哈希链校验以首条保留记录的 prev_hash 为起点，删除前缀不影响后续链的校验。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.repository import Repository  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计留存清理")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    parser.add_argument("--days", type=int, default=180, help="保留天数，默认 180")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不删除")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    if args.days < 1:
        print("--days 必须 >= 1", file=sys.stderr)
        return 2
    repository = Repository(args.db or settings.db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    stale = repository.count_audit_events_before(cutoff)
    deleted = 0 if args.dry_run else repository.delete_audit_events_before(cutoff)
    result = {
        "days": args.days,
        "cutoff": cutoff,
        "dry_run": args.dry_run,
        "would_delete": stale,
        "deleted": deleted,
        "remaining": repository.count_audit_events(),
    }
    print(json.dumps(result, ensure_ascii=False) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
