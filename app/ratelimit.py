"""进程内滑动窗口限流：用于登录失败与注册尝试。

单实例部署足够；多实例需改为共享存储（如 Redis），本轮不引入外部依赖。
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int, clock=time.monotonic):
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float] | None:
        hits = self._hits.get(key)
        if hits is None:
            return None
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
            return None
        return hits

    def retry_after(self, key: str) -> int:
        """返回还需等待的秒数；0 表示当前允许尝试。"""
        with self._lock:
            now = self._clock()
            hits = self._prune(key, now)
            if hits is None or len(hits) < self.limit:
                return 0
            return max(1, math.ceil(hits[0] + self.window_seconds - now))

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            hits = self._prune(key, now)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            hits.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
