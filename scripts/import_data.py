#!/usr/bin/env python3
"""校验并幂等导入景点/攻略：python scripts/import_data.py spots data.jsonl --dry-run"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.imports.cli import default_db_path, execute_import


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["spots", "guides"], help="导入景点或攻略")
    parser.add_argument("source", type=Path, help="UTF-8 编码的 .jsonl 或 .csv")
    parser.add_argument("--db", type=Path, default=default_db_path(), help="SQLite 路径，默认读取 TRAVELAI_DB_PATH")
    parser.add_argument("--dry-run", action="store_true", help="只校验并输出报告，不创建或修改数据库")
    parser.add_argument("--report", type=Path, help="把同样的 JSON 报告保存到指定文件")
    args = parser.parse_args()

    report = execute_import(args.kind, args.source, args.db, args.dry_run)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    if report["errors"]:
        print("导入未执行：请先修正报告中的全部错误。", file=sys.stderr)
        return 2
    if args.dry_run:
        print("预检通过：未创建或修改数据库。", file=sys.stderr)
    else:
        print(
            f"导入完成：新增 {report['inserted']}，更新 {report['updated']}，跳过 {report['unchanged']}。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
