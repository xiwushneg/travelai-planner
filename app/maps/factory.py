"""按进程配置装配地图服务；AK 不进入前端或行程数据。"""
from __future__ import annotations

from app.maps.base import BaiduRouteProvider, LocalRouteProvider, MockRouteProvider


def build_map_provider(settings):
    requested = settings.map_provider
    if requested == "local":
        return LocalRouteProvider(), "local_estimate"
    if requested == "mock":
        return MockRouteProvider(), "mock_no_network"
    if requested == "baidu":
        if not settings.baidu_map_ak:
            return LocalRouteProvider(), "local_fallback_baidu_ak_missing"
        try:
            return BaiduRouteProvider(settings.baidu_map_ak, settings.map_timeout_seconds, settings.baidu_route_mode), "baidu_directionlite_configured"
        except ValueError:
            return LocalRouteProvider(), "local_fallback_invalid_baidu_config"
    return LocalRouteProvider(), f"local_fallback_unknown_provider:{requested}"
