"""可替换的路线估算提供者；外部地图不是本地排程的正确性前提。"""
from __future__ import annotations

import json
import math
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from app.core.geo import haversine_km
from app.core.models import TransportSettings


class MapProviderError(RuntimeError):
    """不包含 AK 或完整请求 URL 的安全错误。"""


class RouteResult(BaseModel):
    distance_km: float = Field(ge=0)
    travel_minutes: int = Field(ge=0)
    basis: str
    provider: str
    is_online: bool = False


class RouteProvider(Protocol):
    name: str
    online: bool

    def estimate(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        transport: TransportSettings,
    ) -> RouteResult:
        ...


class LocalRouteProvider:
    """与原排程一致：球面距离、固定平均速度和可配置缓冲。"""

    name = "local_estimate"
    online = False

    def estimate(self, origin, destination, transport: TransportSettings) -> RouteResult:
        distance = haversine_km(origin[0], origin[1], destination[0], destination[1])
        minutes = math.ceil(distance / transport.average_speed_kmh * 60) + transport.buffer_minutes
        return RouteResult(
            distance_km=round(distance, 3),
            travel_minutes=minutes,
            basis="haversine_estimate",
            provider=self.name,
        )


class MockRouteProvider:
    """确定性的离线测试适配器，不访问网络。"""

    name = "mock"
    online = False

    def __init__(self, distance_km: float = 10, duration_seconds: int = 900, error: str | None = None):
        self.distance_km = distance_km
        self.duration_seconds = duration_seconds
        self.error = error
        self.calls: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def estimate(self, origin, destination, transport: TransportSettings) -> RouteResult:
        self.calls.append((origin, destination))
        if self.error:
            raise MapProviderError(self.error)
        return RouteResult(
            distance_km=self.distance_km,
            travel_minutes=math.ceil(self.duration_seconds / 60) + transport.buffer_minutes,
            basis="mock_route",
            provider=self.name,
        )


class BaiduRouteProvider:
    """百度 DirectionLite 服务端适配器；一次在线路线结果不等于实时路况。"""

    name = "baidu_directionlite"
    online = True
    endpoint = "https://api.map.baidu.com/directionlite/v1/{service}"

    def __init__(self, ak: str, timeout_seconds: float = 4.0, mode: str = "walking"):
        if not ak or not ak.strip():
            raise ValueError("百度地图适配器需要服务端 AK")
        if mode not in {"walking", "driving", "transit"}:
            raise ValueError("百度路线模式仅支持 walking、driving、transit")
        self._ak = ak.strip()
        self.timeout_seconds = timeout_seconds
        self.mode = mode

    def estimate(self, origin, destination, transport: TransportSettings) -> RouteResult:
        query = urlencode({
            "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
            "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
            "coord_type": "wgs84",
            "ak": self._ak,
        })
        request = Request(
            self.endpoint.format(service=self.mode) + "?" + query,
            headers={"Accept": "application/json", "User-Agent": "TravelAI/1.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
            raise MapProviderError("百度路线服务暂时不可用") from None
        if data.get("status") != 0:
            message = str(data.get("message") or "服务返回错误")[:160]
            raise MapProviderError(f"百度路线服务返回错误：{message}")
        try:
            route = data["result"]["routes"][0]
            distance_m = float(route["distance"])
            duration_s = float(route["duration"])
            if distance_m < 0 or duration_s < 0:
                raise ValueError("negative route values")
        except (KeyError, IndexError, TypeError, ValueError):
            raise MapProviderError("百度路线服务返回了无法识别的路线数据") from None
        return RouteResult(
            distance_km=round(distance_m / 1000, 3),
            travel_minutes=math.ceil(duration_s / 60) + transport.buffer_minutes,
            basis="baidu_directionlite",
            provider=self.name,
            is_online=True,
        )
