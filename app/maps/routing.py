"""带缓存、在线调用预算和显式降级说明的路线估算门户。"""
from __future__ import annotations

from app.core.models import TransportSettings
from app.maps.base import LocalRouteProvider, MapProviderError, RouteProvider, RouteResult


class RouteEstimator:
    def __init__(self, provider: RouteProvider | None = None, max_online_calls: int = 40):
        self.provider = provider or LocalRouteProvider()
        self.max_online_calls = max(0, max_online_calls)
        self.online_calls = 0
        self._cache: dict[tuple, RouteResult] = {}
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def _warn_once(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    def estimate(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        transport: TransportSettings,
    ) -> RouteResult:
        key = (
            round(origin[0], 6), round(origin[1], 6),
            round(destination[0], 6), round(destination[1], 6),
            transport.mode, transport.average_speed_kmh, transport.buffer_minutes,
        )
        if key in self._cache:
            return self._cache[key]
        fallback = LocalRouteProvider()
        use_provider = not self.provider.online or self.online_calls < self.max_online_calls
        if self.provider.online and not use_provider:
            self._warn_once(f"在线路线调用已达到 {self.max_online_calls} 次上限，后续路段使用本地估算。")
        elif self.provider.online:
            self.online_calls += 1
        if use_provider:
            try:
                result = self.provider.estimate(origin, destination, transport)
            except MapProviderError as error:
                self._warn_once(f"{error}；受影响路段已使用本地估算，不能视为实时路线。")
                result = fallback.estimate(origin, destination, transport).model_copy(
                    update={
                        "basis": "haversine_estimate_after_provider_failure",
                        "provider": "local_fallback",
                    }
                )
        else:
            result = fallback.estimate(origin, destination, transport).model_copy(
                update={
                    "basis": "haversine_estimate_after_online_budget",
                    "provider": "local_fallback",
                }
            )
        self._cache[key] = result
        return result
