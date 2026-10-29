"""关键事件审计：只落非敏感字段，写入失败不影响主流程。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Optional

from app.audit_anchor import update_anchor
from app.config import settings

logger = logging.getLogger("travelai.audit")

_FORBIDDEN_KEYWORDS = ("password", "passwd", "token", "cookie", "secret", "authorization")

GENESIS = "GENESIS"


def digest_fields(
    event_id: str, created_at: str, actor_key: Optional[str], event_type: str,
    ip: Optional[str], result: str, detail_json: Optional[str],
) -> list[str]:
    """参与哈希的字段集合；新增字段必须同步更新校验脚本与迁移说明。"""
    return [
        event_id, created_at, actor_key or "", event_type, ip or "", result, detail_json or "",
    ]


def chain_hash(prev_hash: str, fields: list[str]) -> str:
    """HMAC-SHA256(prev_hash + 规范化字段)，用于防篡改哈希链。"""
    payload = prev_hash + "\n" + json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return hmac.new(
        settings.audit_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_audit_chain(rows: list[dict]) -> dict:
    """校验哈希链；旧记录（无 entry_hash）只统计不校验，出现断链即报告首个破损 id。"""
    hashed = legacy = 0
    first_broken = None
    expected_prev = None
    for row in rows:
        if not row["entry_hash"]:
            legacy += 1
            if hashed and first_broken is None:
                first_broken = row["id"]
            continue
        prev_hash = row["prev_hash"] or GENESIS
        if expected_prev is None:
            expected_prev = prev_hash
        recalculated = chain_hash(
            prev_hash,
            digest_fields(
                row["id"], row["created_at"], row["actor_key"], row["event_type"],
                row["ip"], row["result"], row["detail"],
            ),
        )
        if prev_hash != expected_prev or recalculated != row["entry_hash"]:
            first_broken = row["id"]
            break
        expected_prev = row["entry_hash"]
        hashed += 1
    return {
        "rows": len(rows),
        "hashed": hashed,
        "legacy": legacy,
        "first_broken": first_broken,
        "ok": first_broken is None,
    }


def hash_identifier(value: str) -> str:
    """用户标识 HMAC 哈希：密钥由 TRAVELAI_AUDIT_KEY 注入，避免固定盐被字典反查。"""
    key = settings.audit_key.encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _sanitize(detail: Optional[dict]) -> Optional[dict]:
    if not detail:
        return None
    safe = {}
    for key, value in detail.items():
        if any(word in key.lower() for word in _FORBIDDEN_KEYWORDS):
            continue
        if isinstance(value, str) and len(value) > 200:
            value = value[:200]
        safe[key] = value
    return safe or None


def record_audit(
    repository,
    event_type: str,
    *,
    username: Optional[str] = None,
    ip: Optional[str] = None,
    result: str,
    detail: Optional[dict] = None,
) -> Optional[str]:
    actor_key = hash_identifier(username.lower()) if username else None
    try:
        event_id = repository.add_audit_event(
            event_type, actor_key, ip, result, _sanitize(detail)
        )
    except Exception as exc:  # 审计不可用不应阻断登录或请求
        logger.warning("audit write failed event=%s error=%s", event_type, exc)
        return None
    update_anchor(repository)
    return event_id
