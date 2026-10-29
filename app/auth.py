"""认证基础：bcrypt 密码哈希与不透明会话 token。

会话方案为服务端 session：浏览器只持有随机 token（httpOnly Cookie），
数据库仅保存其 SHA-256 摘要，因此登出即可在服务端立即撤销。
"""
from __future__ import annotations

import hashlib
import secrets

import bcrypt

MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 72  # bcrypt 的输入上限
DISABLED_PASSWORD_HASH = "!disabled"  # 保留账户使用，任何明文都不会匹配

# 固定 dummy 哈希（cost=12，与真实口令同一代价）：用户不存在或账号不可用时
# 仍执行一次 bcrypt 校验，使登录失败的耗时分布与真实用户一致。
DUMMY_PASSWORD_HASH = "$2b$12$8CVfulU1REGuAFe9NJm/GOtcI2CxKsMqIFIYVZybri62g/cPZfx4O"


class PasswordPolicyError(ValueError):
    pass


def validate_password(password: str) -> None:
    encoded = password.encode("utf-8")
    if len(encoded) < MIN_PASSWORD_BYTES:
        raise PasswordPolicyError("密码至少 8 个字符。")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordPolicyError("密码不能超过 72 字节。")


def hash_password(password: str) -> str:
    validate_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def _check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def verify_password(password: str, password_hash: str) -> bool:
    """始终消耗一次 bcrypt；不可用哈希走 dummy 分支，避免失败路径的时序差异。"""
    if password_hash and not password_hash.startswith("!"):
        return _check_password(password, password_hash)
    _check_password(password, DUMMY_PASSWORD_HASH)
    return False


def hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_session_token() -> tuple[str, str]:
    """返回（下发给浏览器的原始 token，入库的摘要）。"""
    raw = secrets.token_urlsafe(32)
    return raw, hash_session_token(raw)


def normalize_username(username: str) -> str:
    return username.strip()
