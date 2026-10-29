"""把现有 SQLite 库迁移到「账号归属」结构（幂等）。

步骤：完整性检查 → 备份 → 建 users/sessions → 给 trips/trip_versions/agent_runs
补 owner_id → 历史数据归属保留账户 legacy（不可登录）→ 校验无空归属。

用法：
    python scripts/migrate_auth.py            # 备份后执行迁移
    python scripts/migrate_auth.py --dry-run  # 只打印将要执行的动作
    python scripts/migrate_auth.py --claim-to alice  # 把 legacy 名下数据改判给已有账号
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

from app.repository import LEGACY_USER_ID, Repository  # noqa: E402

OWNED_TABLES = ("trips", "trip_versions", "agent_runs")


def inspect(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trips'"
        ).fetchone()
        if not exists:
            return {"initialized": False}
        info = {"initialized": True, "tables": {}}
        for table in OWNED_TABLES:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            total = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            nulls = (
                conn.execute(f"SELECT count(*) FROM {table} WHERE owner_id IS NULL").fetchone()[0]
                if "owner_id" in columns else None
            )
            info["tables"][table] = {
                "total": total, "has_owner": "owner_id" in columns, "null_owner": nulls,
            }
        info["legacy_user"] = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone() and conn.execute(
            "SELECT count(*) FROM users WHERE id=?", (LEGACY_USER_ID,)
        ).fetchone()[0]
        return info
    finally:
        conn.close()


def integrity_ok(db_path: Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="TravelAI 账号归属迁移")
    parser.add_argument("--db", default=str(ROOT / "data" / "travelai.db"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--claim-to", default=None, help="把 legacy 数据改判给该用户名")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        print(f"未找到数据库：{db_path}\n新库无需迁移，服务启动时会直接创建带归属的表结构。")
        return 0
    if not integrity_ok(db_path):
        print("数据库完整性检查未通过，已中止。请先排查备份与磁盘问题。")
        return 2

    before = inspect(db_path)
    if not before.get("initialized"):
        print("库文件存在但没有 trips 表，视为未初始化；服务启动时会自动建表。")
        return 0

    print(f"目标库：{db_path}")
    for table, stat in before["tables"].items():
        state = "已有 owner_id" if stat["has_owner"] else "缺少 owner_id（将新增并回填）"
        print(f"  {table}: {stat['total']} 行，{state}")

    if args.dry_run:
        print("\n[dry-run] 未写入任何数据。正式执行将：")
        print("  1) 备份到 data/backups/")
        print(f"  2) 创建 users / sessions 表与索引")
        print(f"  3) 新增缺失的 owner_id 列并回填为 {LEGACY_USER_ID}")
        if args.claim_to:
            print(f"  4) 把 legacy 名下数据改判给账号 {args.claim_to}")
        return 0

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{db_path.stem}-{datetime.now():%Y%m%d-%H%M%S}{db_path.suffix}"
    shutil.copy2(db_path, backup)
    print(f"已备份：{backup}")

    repository = Repository(db_path)  # 幂等：建表、补列、回填 legacy
    print("表结构与索引迁移完成。")

    if args.claim_to:
        with repository.connect() as conn:
            user = conn.execute(
                "SELECT id,is_active FROM users WHERE lower(username)=lower(?)",
                (args.claim_to,),
            ).fetchone()
            if not user or not user["is_active"]:
                print(f"目标账号不存在或不可用：{args.claim_to}；跳过改判，数据仍在 legacy 名下。")
                return 1
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in OWNED_TABLES:
                    cursor = conn.execute(
                        f"UPDATE {table} SET owner_id=? WHERE owner_id=?",
                        (user["id"], LEGACY_USER_ID),
                    )
                    print(f"  {table}: {cursor.rowcount} 行改判给 {args.claim_to}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    after = inspect(db_path)
    remaining = {table: stat["null_owner"] for table, stat in after["tables"].items()}
    print("迁移后归属校验：", remaining)
    if any(value for value in remaining.values() if value):
        print("仍有记录缺少归属，请检查上面的输出。")
        return 1
    print("迁移完成，可以启动服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
