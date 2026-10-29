"""登录失败计数与指数退避：状态存 SQLite，跨进程可见。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.repository import Repository

SCOPE_IP = "ip"
SCOPE_ACCOUNT = "account"


def backoff_seconds(fail_count: int, threshold: int, base_seconds: int, max_seconds: int) -> int:
    """失败次数达到阈值后按 2 的幂增长，返回本次应退避的秒数。"""
    if fail_count < threshold:
        return 0
    exponent = fail_count - threshold
    return min(base_seconds * (2 ** exponent), max_seconds)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class LoginGuard:
    def __init__(
        self,
        repository: Repository,
        threshold: int,
        base_seconds: int,
        max_seconds: int,
        decay_seconds: int = 86400,
        block_audit_interval_seconds: int = 60,
    ):
        self.repository = repository
        self.threshold = max(1, threshold)
        self.base_seconds = max(1, base_seconds)
        self.max_seconds = max(self.base_seconds, max_seconds)
        self.decay_seconds = max(1, decay_seconds)
        self.block_audit_interval_seconds = max(1, block_audit_interval_seconds)
        self._block_audit_at: dict[tuple[str, str], float] = {}

    def _active_state(self, scope: str, key: str, now: datetime) -> Optional[dict]:
        state = self.repository.get_login_failure(scope, key)
        if state is None:
            return None
        updated = _parse(state["updated_at"])
        if updated and now - updated > timedelta(seconds=self.decay_seconds):
            # 长时间无失败自动衰减，避免零散误输累积成永久退避。
            self.repository.clear_login_failures(scope, key)
            return None
        return state

    def check(self, ip: str, account: str) -> Optional[tuple[str, int]]:
        """返回 (真实原因, 剩余秒数)；未退避时返回 None。"""
        now = _now()
        for scope, key, reason in (
            (SCOPE_IP, ip, "backoff_ip"),
            (SCOPE_ACCOUNT, account, "backoff_account"),
        ):
            state = self._active_state(scope, key, now)
            blocked_until = _parse(state["blocked_until"]) if state else None
            if blocked_until and blocked_until > now:
                return reason, int((blocked_until - now).total_seconds()) + 1
        return None

    def register_failure(self, ip: str, account: str) -> None:
        """IP 与账号两个维度独立累加；达到阈值后写入退避到期时间。"""
        now = _now()
        for scope, key in ((SCOPE_IP, ip), (SCOPE_ACCOUNT, account)):
            state = self._active_state(scope, key, now)
            if state and _parse(state["blocked_until"]) and _parse(state["blocked_until"]) > now:
                # 退避期间不再累加，避免攻击流量把退避无限拉长。
                continue
            count = self.repository.increment_login_failure(scope, key)
            delay = backoff_seconds(count, self.threshold, self.base_seconds, self.max_seconds)
            if delay:
                until = now + timedelta(seconds=delay)
                self.repository.set_login_block(scope, key, until.isoformat())

    def clear_account(self, account: str) -> None:
        self.repository.clear_login_failures(SCOPE_ACCOUNT, account)

    def should_audit_block(self, ip: str, account: str) -> bool:
        """退避期内同一 IP+账号的审计最多每 interval 秒一条，避免审计表被刷爆。

        进程内节流：多实例部署时每个实例各写一条，数量仍与实例数同阶。
        """
        now = time.monotonic()
        key = (ip, account)
        last = self._block_audit_at.get(key)
        if last is not None and now - last < self.block_audit_interval_seconds:
            return False
        if len(self._block_audit_at) >= 2048:
            self._block_audit_at = {
                item: stamp
                for item, stamp in self._block_audit_at.items()
                if now - stamp < self.block_audit_interval_seconds
            }
        self._block_audit_at[key] = now
        return True
