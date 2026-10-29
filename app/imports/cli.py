"""命令行导入应用逻辑；只有全部记录通过校验后才打开数据库写事务。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.imports.io import ImportFileError, load_records
from app.repository import Repository


def execute_import(kind: str, source: Path, db_path: Path, dry_run: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "kind": kind,
        "source": str(source),
        "database": str(db_path),
        "dry_run": dry_run,
        "committed": False,
        "total_records": 0,
        "valid_records": 0,
        "rejected_records": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": [],
    }
    try:
        records, errors, total = load_records(source, kind)  # type: ignore[arg-type]
    except ImportFileError as error:
        report["errors"] = [{"line": 0, "field": "file", "message": str(error)}]
        return report

    report["total_records"] = total
    report["valid_records"] = len(records)
    report["errors"] = errors
    if total == 0 and not errors:
        report["errors"] = [{"line": 0, "field": "file", "message": "没有可导入的数据记录"}]
    if report["errors"]:
        report["rejected_records"] = total
        return report
    if dry_run:
        return report

    counts = Repository(db_path).import_catalog(kind, records)  # type: ignore[arg-type]
    report.update(counts)
    report["committed"] = True
    return report


def default_db_path() -> Path:
    return Path(os.getenv("TRAVELAI_DB_PATH", "./data/travelai.db"))
