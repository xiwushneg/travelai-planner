"""JSONL / CSV 文件读取、逐行校验和清晰错误定位。"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.imports.validation import ImportGuide, ImportSpot

ImportKind = Literal["spots", "guides"]
SPOT_FIELDS = {
    "id", "name", "city", "category", "latitude", "longitude", "address", "rating",
    "visit_duration_minutes", "tags", "adult_ticket_cny", "child_ticket_cny", "senior_ticket_cny",
    "opening", "source.status", "source.name", "source.url", "source.last_verified_at", "source.notes",
}
GUIDE_FIELDS = {
    "id", "title", "city", "tags", "content", "source.status", "source.name",
    "source.url", "source.last_verified_at", "source.notes",
}
REQUIRED = {
    "spots": {"id", "name", "city", "category", "adult_ticket_cny", "opening", "source.status", "source.name"},
    "guides": {"id", "title", "city", "content", "source.status", "source.name"},
}
JSON_LIST_FIELDS = {"tags"}
OPTIONAL_BLANK = {
    "address", "child_ticket_cny", "senior_ticket_cny",
    "source.url", "source.last_verified_at", "source.notes",
}


class ImportFileError(ValueError):
    pass


def _issue(line: int, field: str, message: str) -> dict[str, Any]:
    return {"line": line, "field": field, "message": message}


def _validation_issues(line: int, error: ValidationError) -> list[dict[str, Any]]:
    return [_issue(line, ".".join(str(part) for part in item["loc"]) or "record", item["msg"]) for item in error.errors()]


def _parse_list(value: str) -> list:
    if not value:
        return []
    if value.startswith("["):
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError("tags 必须是 JSON 数组或竖线分隔文本")
        return parsed
    return [part.strip() for part in value.split("|") if part.strip()]


def _csv_record(row: dict[str, str | None]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for raw_field, raw_value in row.items():
        field = raw_field.strip()
        value = "" if raw_value is None else raw_value.strip()
        target = record
        parts = field.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        key = parts[-1]
        if field in JSON_LIST_FIELDS:
            target[key] = _parse_list(value)
        elif field == "opening":
            target[key] = json.loads(value) if value else {}
        elif not value and field in OPTIONAL_BLANK:
            target[key] = None
        else:
            target[key] = value
    return record


def load_records(path: Path, kind: ImportKind):
    """返回 (有效记录, 错误, 总记录数)；有错误时调用方不得写库。"""
    if kind not in {"spots", "guides"}:
        raise ImportFileError("kind 只能是 spots 或 guides")
    if not path.is_file():
        raise ImportFileError(f"数据文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix not in {".jsonl", ".csv"}:
        raise ImportFileError("仅支持 .jsonl 或 .csv 文件")
    records, errors, seen = [], [], {}
    total = 0

    def add(raw: Any, line: int) -> None:
        nonlocal total
        total += 1
        if not isinstance(raw, dict):
            errors.append(_issue(line, "record", "每条记录必须是 JSON 对象"))
            return
        model = ImportSpot if kind == "spots" else ImportGuide
        try:
            record = model.model_validate(raw)
        except ValidationError as error:
            errors.extend(_validation_issues(line, error))
            return
        if record.id in seen:
            errors.append(_issue(line, "id", f"重复 id；首次出现在第 {seen[record.id]} 行"))
            return
        seen[record.id] = line
        records.append(record)

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            if suffix == ".jsonl":
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as error:
                        errors.append(_issue(line_number, "json", f"JSON 格式错误：{error.msg}"))
                        continue
                    add(raw, line_number)
            else:
                reader = csv.DictReader(stream)
                headers = reader.fieldnames
                if not headers:
                    raise ImportFileError("CSV 缺少表头")
                normalized = [header.strip() if header else "" for header in headers]
                if any(not header for header in normalized):
                    raise ImportFileError("CSV 存在空列名")
                if len(set(normalized)) != len(normalized):
                    raise ImportFileError("CSV 存在重复列名")
                allowed = SPOT_FIELDS if kind == "spots" else GUIDE_FIELDS
                unknown = sorted(set(normalized) - allowed)
                missing = sorted(REQUIRED[kind] - set(normalized))
                if unknown or missing:
                    details = []
                    if unknown:
                        details.append("未知列：" + ", ".join(unknown))
                    if missing:
                        details.append("缺少必需列：" + ", ".join(missing))
                    raise ImportFileError("；".join(details))
                for row in reader:
                    line_number = reader.line_num
                    if None in row:
                        errors.append(_issue(line_number, "csv", "该行列数多于表头"))
                        continue
                    if not any(str(value or "").strip() for value in row.values()):
                        continue
                    try:
                        raw = _csv_record({(key or "").strip(): value for key, value in row.items()})
                    except (ValueError, json.JSONDecodeError) as error:
                        errors.append(_issue(line_number, "csv", str(error)))
                        continue
                    add(raw, line_number)
    except UnicodeDecodeError:
        raise ImportFileError("文件必须使用 UTF-8 编码") from None
    except csv.Error as error:
        raise ImportFileError(f"CSV 无法解析：{error}") from None
    return records, errors, total
