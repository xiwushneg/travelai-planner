"""审计外部锚点：把哈希链水位写到数据库文件之外，用于发现“整库回滚到旧备份”。

哈希链只能发现单条记录被改写或删除；如果攻击者直接用旧备份覆盖整个数据库，
旧库自身的链依然自洽，本地校验无法察觉。锚点把“曾经达到的最大 rowid 与链头哈希”
记在数据库旁边的独立文件里（HMAC 签名，密钥同 TRAVELAI_AUDIT_KEY），回滚后的水位
低于锚点即可判定为可疑回滚。

已知边界：锚点文件与数据库同机存放，若攻击者连同锚点文件一起回滚或删除，仍无法识别；
更强的保证需要把锚点定期推送到外部只读存储。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger("travelai.audit")

ANCHOR_SUFFIX = ".audit-anchor.json"
VERSION = 1
_SIGNED_FIELDS = ("version", "max_rowid", "event_count", "head_hash", "updated_at")


def anchor_path(db_path: str | Path) -> Path:
    """锚点与数据库同目录、独立文件名：只还原 .db 文件不会带回旧锚点。"""
    return Path(f"{db_path}{ANCHOR_SUFFIX}")


def _mac(payload: dict) -> str:
    canonical = json.dumps(
        {key: payload.get(key) for key in _SIGNED_FIELDS},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hmac.new(
        settings.audit_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def read_anchor(db_path: str | Path) -> dict:
    """读取并校验锚点；不存在返回 present=False，签名不符返回 valid=False。"""
    path = anchor_path(db_path)
    if not path.exists():
        return {"present": False, "valid": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": True, "valid": False, "error": f"unreadable: {exc}"}
    if not isinstance(data, dict) or data.get("mac") != _mac(data):
        return {"present": True, "valid": False, "error": "mac_mismatch"}
    anchor = {"present": True, "valid": True}
    anchor.update({key: data.get(key) for key in _SIGNED_FIELDS})
    return anchor


def write_anchor(db_path: str | Path, watermark: dict, *, force: bool = False) -> dict:
    """推进锚点；只允许前进，避免并发写入让水位回退。

    force=True 用于运维显式重建（如审计密钥轮换后签名失效），普通写入永不覆盖
    已存在但签名不符的锚点，防止篡改被新数据洗掉。
    """
    path = anchor_path(db_path)
    current = read_anchor(db_path)
    if current["present"] and not current["valid"] and not force:
        logger.warning("审计锚点校验失败，未覆盖：%s", current.get("error"))
        return {"updated": False, "skipped": "anchor_invalid"}
    if current.get("valid") and not force and watermark["max_rowid"] <= current["max_rowid"]:
        return {"updated": False, "skipped": "not_advanced"}
    payload = {
        "version": VERSION,
        "max_rowid": watermark["max_rowid"],
        "event_count": watermark["event_count"],
        "head_hash": watermark["head_hash"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["mac"] = _mac(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return {"updated": True, "max_rowid": watermark["max_rowid"]}


def update_anchor(repository) -> dict:
    """每个审计事件写入后调用；失败只告警，不影响主流程。"""
    try:
        return write_anchor(repository.path, repository.audit_watermark())
    except Exception as exc:
        logger.warning("audit anchor write failed error=%s", exc)
        return {"updated": False, "skipped": "error"}


def compare_watermark(
    anchor: dict, watermark: dict, hash_at_anchor: Optional[str]
) -> dict:
    """对比数据库水位与锚点，判定是否疑似整库回滚/改写。

    anchor_row_pruned：锚点行已被留存清理删除，降级为提示（链校验仍会兜住改写）。
    """
    if not anchor.get("present"):
        return {"status": "missing_anchor", "rollback_suspected": False}
    if not anchor.get("valid"):
        return {"status": "invalid_anchor", "rollback_suspected": False}
    if watermark["max_rowid"] < anchor["max_rowid"]:
        return {"status": "rollback", "rollback_suspected": True}
    if watermark["max_rowid"] == anchor["max_rowid"]:
        if anchor["max_rowid"] and hash_at_anchor != anchor["head_hash"]:
            return {"status": "fork", "rollback_suspected": True}
        return {"status": "ok", "rollback_suspected": False}
    if hash_at_anchor is None:
        return {"status": "anchor_row_pruned", "rollback_suspected": False}
    if hash_at_anchor != anchor["head_hash"]:
        return {"status": "fork", "rollback_suspected": True}
    return {"status": "anchor_stale", "rollback_suspected": False}
