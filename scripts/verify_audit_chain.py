"""校验审计哈希链与外部锚点（只读）。

用法：
    python scripts/verify_audit_chain.py
    python scripts/verify_audit_chain.py --json
    python scripts/verify_audit_chain.py --update-anchor   # 运维显式推进/重建锚点

数据库以只读 URI 打开；断链或疑似整库回滚时退出码为 1。
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
from app.audit_anchor import compare_watermark, read_anchor, write_anchor  # noqa: E402
from app.config import settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验审计哈希链与外部锚点（只读）")
    parser.add_argument("--db", help="数据库路径，默认使用应用配置")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--update-anchor", action="store_true",
        help="按当前库水位重建/推进锚点（审计密钥轮换后需显式执行）",
    )
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
        head = conn.execute(
            "SELECT rowid AS rid, entry_hash FROM audit_events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        watermark = {
            "max_rowid": head["rid"] if head else 0,
            "event_count": len(rows),
            "head_hash": head["entry_hash"] if head else None,
        }
        anchor = read_anchor(db_path)
        hash_at_anchor = None
        if anchor.get("valid") and anchor.get("max_rowid"):
            row = conn.execute(
                "SELECT entry_hash FROM audit_events WHERE rowid=?", (anchor["max_rowid"],)
            ).fetchone()
            hash_at_anchor = row["entry_hash"] if row else None
    except sqlite3.OperationalError as exc:
        print(f"查询失败（审计表或哈希列不存在，请先运行 migrate_audit_chain.py）：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    report = verify_audit_chain(rows)
    verdict = compare_watermark(anchor, watermark, hash_at_anchor)
    report.update({
        "watermark": watermark,
        "anchor": {key: anchor.get(key) for key in ("present", "valid", "max_rowid", "event_count", "head_hash", "updated_at", "error")},
        "rollback": verdict,
    })

    if args.update_anchor:
        if not report["ok"]:
            print("拒绝重建锚点：哈希链已断，请先排查篡改。", file=sys.stderr)
            return 1
        result = write_anchor(db_path, watermark, force=True)
        report["anchor_updated"] = result

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        status = "OK" if report["ok"] else f"BROKEN at {report['first_broken']}"
        print(
            f"{status}  rows={report['rows']} hashed={report['hashed']} legacy={report['legacy']}  "
            f"anchor={verdict['status']} watermark_rowid={watermark['max_rowid']}"
        )
        if verdict["status"] == "rollback":
            print(
                f"疑似整库回滚：当前最大 rowid={watermark['max_rowid']} 低于锚点 "
                f"{anchor['max_rowid']}（{anchor.get('updated_at')}）",
                file=sys.stderr,
            )
        elif verdict["status"] == "invalid_anchor":
            print(
                "锚点签名校验失败（密钥变更或文件被改写）；确认无误后执行 --update-anchor 重建。",
                file=sys.stderr,
            )
        elif verdict["status"] == "missing_anchor":
            print("锚点文件缺失：无法检测整库回滚，建议执行 --update-anchor 建立基线。", file=sys.stderr)

    failed = not report["ok"] or verdict["rollback_suspected"]
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
