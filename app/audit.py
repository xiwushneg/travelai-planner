"""关键事件审计：只落非敏感字段，写入失败不影响主流程。"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger("travelai.audit")

_FORBIDDEN_KEYWORDS = ("password", "passwd", "token", "cookie", "secret", "authorization")


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
        return repository.add_audit_event(
            event_type, actor_key, ip, result, _sanitize(detail)
        )
    except Exception as exc:  # 审计不可用不应阻断登录或请求
        logger.warning("audit write failed event=%s error=%s", event_type, exc)
        return None
